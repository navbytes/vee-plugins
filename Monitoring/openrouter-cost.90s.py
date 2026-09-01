#!/usr/bin/env python3
#
# openrouter-cost.90s.py — OpenRouter account spend in the menu bar.
#
# Data comes from the OpenRouter API (https://openrouter.ai/api/v1/key,
# /api/v1/credits, and /api/v1/activity), so it is harness-agnostic: any
# tool that bills through your OpenRouter key is counted. With a regular
# inference key you get today/week/month for that key plus account all-time;
# a management key additionally unlocks /api/v1/activity, which powers the
# 14-day daily-history sparkline and a per-model donut for today. If no
# token is configured (or the API is unreachable and no cache exists), the
# plugin falls back to OpenCode's local SQLite database, read-only.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>OpenRouter Cost</vee.title>
# <vee.version>2.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>OpenRouter AI spend straight from the API: today, week, month, all-time, key limit, with a 14-day sparkline and per-model donut (management key). Falls back to local OpenCode data.</vee.desc>
# <vee.dependencies>python3</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.var>string(OPENROUTER_API_TOKEN=): Your OpenRouter API key (sk-or-...). Stored in the Keychain and masked in Settings (the name contains "token"). Leave empty to fall back to local OpenCode data.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>network,secrets,filesystem.read,filesystem.write</vee.capabilities>
# <vee.network>https://openrouter.ai only — your own account endpoints, authenticated with your key. Nothing else is contacted.</vee.network>
# <vee.secrets>OPENROUTER_API_TOKEN</vee.secrets>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/openrouter-cost-cache.json (last good API response, shown with a "cached" note if a later run can't reach openrouter.ai)</vee.filesystem.write>
# <vee.filesystem.read>~/.local/share/opencode/opencode.db (fallback only, opened read-only)</vee.filesystem.read>
#

import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://openrouter.ai/api/v1"
DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")
CACHE_FILE = os.path.join(
    os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH") or os.environ.get("TMPDIR") or "/tmp",
    "openrouter-cost-cache.json",
)
STALE_AFTER = 6 * 3600

DIM = "#8E8E93"
GREEN = "#30D158"
YELLOW = "#FFD60A"
RED = "#FF453A"
DONUT_COLORS = ["#0A84FF", "#30D158", "#FFD60A", "#BF5AF2", "#FF453A", "#64D2FF"]


class JSONSection:
    def __init__(self, items):
        self._items = items

    def item(self, text, **opts):
        self._items.append({"text": text, **{k: v for k, v in opts.items() if v is not None}})
        return self

    def separator(self):
        self._items.append({"separator": True})
        return self

    def submenu(self, text, **opts):
        children = []
        self._items.append({"text": text, **{k: v for k, v in opts.items() if v is not None}, "submenu": children})
        return JSONSection(children)


class JSONMenu:
    def __init__(self):
        self._titles = []
        self._items = []

    def title(self, text, **opts):
        self._titles.append({"text": text, **{k: v for k, v in opts.items() if v is not None}})
        return self

    @property
    def dropdown(self):
        return JSONSection(self._items)

    def print(self):
        payload = {"vee": 1, "title": self._titles}
        if self._items:
            payload["items"] = self._items
        print(json.dumps(payload, ensure_ascii=False))


def compact(n):
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


def money(c):
    return f"${c:.2f}"


def spend_color(c):
    return DIM if c <= 0 else (GREEN if c < 1 else (YELLOW if c < 5 else RED))


# ---------------------------------------------------------------------------
# OpenRouter API
# ---------------------------------------------------------------------------

def resolve_token():
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_API_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def api_get(path, token):
    req = urllib.request.Request(
        f"{API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]


def shape_activity(rows):
    """14-day daily series (UTC) and today's per-model breakdown."""
    today = datetime.now(timezone.utc).date()
    by_day, by_model = {}, {}
    for row in rows:
        by_day[row["date"]] = by_day.get(row["date"], 0.0) + row.get("usage", 0.0)
        if row["date"] == today.isoformat():
            m = by_model.setdefault(row.get("model") or "unknown", [0.0, 0])
            m[0] += row.get("usage", 0.0)
            m[1] += row.get("requests", 0)
    series = [by_day.get((today - timedelta(days=i)).isoformat(), 0.0) for i in range(13, -1, -1)]
    models = sorted(((name, v[0], v[1]) for name, v in by_model.items()), key=lambda x: -x[1])
    return {"daily": series, "models": models}


def fetch_openrouter(token):
    key = api_get("/key", token)
    credits = api_get("/credits", token)
    data = {
        "key": key,
        "credits": credits,
        "activity": None,
    }
    if key.get("is_management_key"):
        try:
            data["activity"] = shape_activity(api_get("/activity", token))
        except (urllib.error.URLError, json.JSONDecodeError, KeyError):
            pass  # history is optional; totals still render
    return data


def cached_payload():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def openrouter_payload(token):
    """Fresh API data, or a cached copy (with stale flag) if offline."""
    try:
        payload = {"ts": time.time(), "stale": False, "api": fetch_openrouter(token)}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, CACHE_FILE)
        return payload
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise  # auth problems must surface, not hide behind a stale cache
        cached = cached_payload()
        if cached and time.time() - cached.get("ts", 0) < STALE_AFTER:
            cached["stale"] = True
            cached["error"] = str(e)
            return cached
        raise
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
        cached = cached_payload()
        if cached and time.time() - cached.get("ts", 0) < STALE_AFTER:
            cached["stale"] = True
            cached["error"] = str(e)
            return cached
        raise


# ---------------------------------------------------------------------------
# Local OpenCode fallback
# ---------------------------------------------------------------------------

def open_db_ro():
    """(connection, scratch_dir_or_None). A read-only open can fail on a WAL
    database when the -shm file must be (re)created; fall back to querying a
    scratch copy. Caller removes scratch_dir after closing the connection."""
    uri = f"file:{DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        conn.execute("SELECT 1 FROM session LIMIT 1")
        return conn, None
    except sqlite3.Error:
        pass
    scratch = tempfile.mkdtemp(prefix="vee-opencode-")
    for suffix in ("", "-wal", "-shm"):
        src = DB_PATH + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(scratch, "copy.db" + suffix))
    conn = sqlite3.connect(os.path.join(scratch, "copy.db"), timeout=2)
    return conn, scratch


def local_stats():
    conn, scratch = open_db_ro()
    try:
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731
        d0, d7, d30 = ms(midnight), ms(midnight - timedelta(days=7)), ms(midnight - timedelta(days=30))

        msg = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.cost') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.cost') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.cost') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.tokens.input') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.tokens.output') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.tokens.cache.read') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.tokens.cache.write') END), 0),
              COALESCE(SUM(CASE WHEN m.time_created >= ? THEN json_extract(m.data, '$.tokens.reasoning') END), 0)
            FROM message m
            WHERE json_extract(m.data, '$.role') = 'assistant'
              AND m.time_created >= ?
            """,
            (d0, d7, d30, d0, d0, d0, d0, d0, d30),
        ).fetchone()

        projects = conn.execute(
            """
            SELECT COALESCE(p.name, p.worktree), SUM(json_extract(m.data, '$.cost')), COUNT(DISTINCT m.session_id)
            FROM message m
            JOIN session s ON s.id = m.session_id
            JOIN project p ON p.id = s.project_id
            WHERE json_extract(m.data, '$.role') = 'assistant'
              AND m.time_created >= ?
            GROUP BY s.project_id
            ORDER BY 2 DESC
            LIMIT 8
            """,
            (d0,),
        ).fetchall()

        projects = [p for p in projects if p[0] and p[1] >= 0.005]

        d14 = ms(midnight - timedelta(days=13))
        daily = dict(
            conn.execute(
                """
                SELECT date(m.time_created / 1000, 'unixepoch', 'localtime'),
                       SUM(json_extract(m.data, '$.cost'))
                FROM message m
                WHERE json_extract(m.data, '$.role') = 'assistant'
                  AND m.time_created >= ?
                GROUP BY 1
                """,
                (d14,),
            ).fetchall()
        )
        series = []
        for i in range(13, -1, -1):
            day = (midnight - timedelta(days=i)).date().isoformat()
            series.append(daily.get(day, 0.0))

        all_time = conn.execute(
            """
            SELECT COALESCE(SUM(cost), 0), COALESCE(SUM(tokens_input), 0),
                   COALESCE(SUM(tokens_output), 0), COUNT(*)
            FROM session
            """
        ).fetchone()

        today, week, month = msg[0], msg[1], msg[2]
        tok = {
            "in": int(msg[3] or 0), "out": int(msg[4] or 0),
            "cache_read": int(msg[5] or 0), "cache_write": int(msg[6] or 0),
            "reasoning": int(msg[7] or 0),
        }
        return {
            "today": today, "week": week, "month": month, "tokens": tok,
            "projects": [(p[0].rstrip("/").rsplit("/", 1)[-1], p[1], p[2]) for p in projects],
            "daily": series,
            "all": all_time[0], "all_in": int(all_time[1]), "all_out": int(all_time[2]),
            "sessions": int(all_time[3]),
        }
    finally:
        conn.close()
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def donut(names, costs):
    if len(costs) > 5:
        names, costs = names[:4] + ["Other"], costs[:4] + [sum(costs[4:])]
    return {
        "kind": "donut",
        "values": costs,
        "labels": names,
        "colors": [DONUT_COLORS[i % len(DONUT_COLORS)] for i in range(len(costs))],
    }


def render_api(data, stale, error=None):
    menu = JSONMenu()
    dropdown = menu.dropdown
    key, credits = data["key"], data["credits"]
    today = key.get("usage_daily") or 0.0
    menu.title(money(today), color=spend_color(today), sfimage="dollarsign.circle",
               tooltip=f"OpenRouter spend today (this key, UTC) · account all-time {money(credits.get('total_usage') or 0.0)}")

    if stale:
        dropdown.item(f"Cached — API unreachable ({error or 'offline'})", color=YELLOW, sfimage="clock.arrow.circlepath")

    dropdown.item("Today (this key, UTC)", color=DIM)
    dropdown.item(f"  {money(today)}")
    if key.get("limit"):
        reset = key.get("limit_reset") or "no reset"
        dropdown.item(f"  Limit {money(key['limit'])} · {reset} · {money(key.get('limit_remaining') or 0.0)} left")
    dropdown.separator()
    dropdown.item(f"This week · {money(key.get('usage_weekly') or 0.0)}")
    dropdown.item(f"This month · {money(key.get('usage_monthly') or 0.0)}")
    dropdown.item(f"All time (key) · {money(key.get('usage') or 0.0)}", color=DIM)
    dropdown.item(f"All time (account) · {money(credits.get('total_usage') or 0.0)}", color=DIM)
    if credits.get("total_credits"):
        remaining = credits["total_credits"] - (credits.get("total_usage") or 0.0)
        dropdown.item(f"  Credits remaining · {money(max(0.0, remaining))}", color=DIM)

    activity = data.get("activity")
    dropdown.separator()
    if activity:
        dropdown.item(
            f"Daily spend · last 14 days ({money(sum(activity['daily']))})",
            sparkline=activity["daily"],
            sparklineColor="#0A84FF",
            accessoryWidth=140,
            accessoryHeight=14,
        )
        if activity["models"]:
            names = [m[0] for m in activity["models"]]
            costs = [m[1] for m in activity["models"]]
            dropdown.item("Today by model", chart=donut(names, costs),
                          accessoryWidth=48, accessoryHeight=48)
            for name, cost, requests in activity["models"]:
                dropdown.submenu(f"  {money(cost)}  {name}").item(
                    f"{requests} request{'s' if requests != 1 else ''} today (UTC)", color=DIM)
    else:
        dropdown.item("Daily history needs a management key", color=DIM)
        dropdown.item("  Create one at openrouter.ai/keys → Management Key, then update this plugin's token.", color=DIM, size=11)
    menu.print()


def render_local(data, note=None):
    menu = JSONMenu()
    dropdown = menu.dropdown
    today = data["today"]
    menu.title(money(today), color=spend_color(today), sfimage="dollarsign.circle",
               tooltip=f"OpenCode spend today (local database) · all-time {money(data['all'])}")

    if note:
        dropdown.item(note, color=YELLOW)
    dropdown.item(f"Local OpenCode data ({datetime.now().astimezone().strftime('%a %b %-d')})", color=DIM)
    t = data["tokens"]
    dropdown.item(f"  {money(today)}  ·  {compact(t['in'])} in / {compact(t['out'])} out")
    dropdown.item(f"  cache {compact(t['cache_read'])} read / {compact(t['cache_write'])} write  ·  {compact(t['reasoning'])} reasoning")
    dropdown.separator()
    dropdown.item(f"Last 7 days · {money(data['week'])}")
    dropdown.item(f"Last 30 days · {money(data['month'])}")
    dropdown.item(f"All time · {money(data['all'])}", color=DIM)
    dropdown.item(f"  {compact(data['all_in'])} in / {compact(data['all_out'])} out across {data['sessions']} sessions", color=DIM)

    dropdown.separator()
    dropdown.item(
        f"Daily spend · last 14 days ({money(sum(data['daily']))})",
        sparkline=data["daily"],
        sparklineColor="#0A84FF",
        accessoryWidth=140,
        accessoryHeight=14,
    )

    if data["projects"]:
        dropdown.separator()
        dropdown.item("Today by project",
                      chart=donut([p[0] for p in data["projects"]], [p[1] for p in data["projects"]]),
                      accessoryWidth=48, accessoryHeight=48)
        for name, cost, sessions in data["projects"]:
            dropdown.submenu(f"  {money(cost)}  {name}").item(
                f"{sessions} session{'s' if sessions != 1 else ''} today", color=DIM)
    menu.print()


def render_unavailable(reason, hint=None):
    menu = JSONMenu()
    menu.title("$—", color=DIM, sfimage="dollarsign.circle", tooltip="OpenRouter Cost")
    menu.dropdown.item(reason, color=DIM)
    if hint:
        menu.dropdown.item(hint, color=DIM, size=11)
    menu.print()


def main():
    token = resolve_token()
    if token:
        try:
            payload = openrouter_payload(token)
            render_api(payload["api"], payload.get("stale", False), payload.get("error"))
            return
        except Exception as e:  # noqa: BLE001 — any API failure degrades to local
            api_error = str(e)
        else:
            api_error = None
    else:
        api_error = None

    if not os.path.exists(DB_PATH):
        if api_error:
            render_unavailable(f"OpenRouter API failed: {api_error}",
                               "No fallback: OpenCode database not found.")
        else:
            render_unavailable("No OpenRouter token configured",
                               "Set OPENROUTER_API_TOKEN in this plugin's Settings.")
        return
    try:
        note = (f"OpenRouter API failed — showing local data ({api_error})"
                if api_error else
                "No OpenRouter token — local OpenCode data only. Set OPENROUTER_API_TOKEN in Settings.")
        render_local(local_stats(), note=note)
    except sqlite3.Error as e:
        render_unavailable(f"Could not read OpenCode data: {e}")


if __name__ == "__main__":
    main()
