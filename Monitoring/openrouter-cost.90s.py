#!/usr/bin/env python3
#
# openrouter-cost.90s.py — OpenRouter account spend in the menu bar.
#
# Data comes from the OpenRouter API (https://openrouter.ai/api/v1/key,
# /api/v1/credits, and /api/v1/activity), so it is harness-agnostic: any
# tool that bills through your OpenRouter key is counted. With a regular
# inference key you get today/week/month for that key plus account all-time;
# a management key additionally unlocks /api/v1/activity — account-wide
# numbers: today's headline, 14-day daily sparkline, per-model donut and
# mix, and a week total. Without one, the plugin shows the key's own spend
# and accumulates its own 7-day daily sparkline in the cache.
# If no token is configured or the API is unreachable with no cache, the
# plugin shows an explicit unavailable state.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>OpenRouter Cost</vee.title>
# <vee.version>3.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>OpenRouter spend in the menu bar: account-wide today, credits gauge, 14-day sparkline, per-model donut and mix with a management key — or per-key numbers with a self-built 7-day sparkline on a regular key.</vee.desc>
# <vee.dependencies>python3</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.var>string(OPENROUTER_API_TOKEN=): Your OpenRouter API key (sk-or-...). Stored in the Keychain and masked in Settings (the name contains "token"). Leave empty to show an unavailable state.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>network,secrets,filesystem.write</vee.capabilities>
# <vee.network>https://openrouter.ai only — your own account endpoints, authenticated with your key. Nothing else is contacted.</vee.network>
# <vee.secrets>OPENROUTER_API_TOKEN</vee.secrets>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/openrouter-cost-cache.json (last good API response + 7-day spend ledger, shown with a "cached" note if a later run can't reach openrouter.ai)</vee.filesystem.write>
#

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://openrouter.ai/api/v1"
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


def depletion_color(share):
    return GREEN if share < 0.5 else (YELLOW if share < 0.8 else RED)


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
    """Account-wide 14-day daily series (UTC) and per-model breakdowns.
    /activity dates look like '2026-09-01 00:00:00' — normalize to the day."""
    today = datetime.now(timezone.utc).date()
    by_day, by_model, week_models = {}, {}, {}
    for row in rows:
        day = (row.get("date") or "")[:10]
        usage = row.get("usage", 0.0) or 0.0
        name = row.get("model") or "unknown"
        if day:
            by_day[day] = by_day.get(day, 0.0) + usage
        week_models[name] = week_models.get(name, 0.0) + usage
        if day == today.isoformat():
            m = by_model.setdefault(name, [0.0, 0])
            m[0] += usage
            m[1] += row.get("requests", 0) or 0
    series = [by_day.get((today - timedelta(days=i)).isoformat(), 0.0) for i in range(13, -1, -1)]
    models = sorted(((name, v[0], v[1]) for name, v in by_model.items()), key=lambda x: -x[1])
    week = sorted(week_models.items(), key=lambda kv: -kv[1])
    return {
        "daily": series,
        "models": models,
        "week_models": week,
        "today_total": series[-1],
        "today_reqs": sum(m[2] for m in models),
        "today_tok": sum(
            (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) + (r.get("reasoning_tokens") or 0)
            for r in rows if (r.get("date") or "")[:10] == today.isoformat()
        ),
        "week7": sum(series[7:]),
    }


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


def merge_history(history, today_usage):
    """7-day ledger of per-key usage_daily, keyed by UTC date (self-built
    since /key exposes no daily series and /activity needs a management key)."""
    today = datetime.now(timezone.utc).date()
    history = dict(history)
    history[today.isoformat()] = today_usage
    cutoff = (today - timedelta(days=6)).isoformat()
    return {d: v for d, v in sorted(history.items()) if d >= cutoff}


def openrouter_payload(token):
    """Fresh API data, or a cached copy (with stale flag) if offline."""
    try:
        api = fetch_openrouter(token)
        api["history"] = merge_history(
            (cached_payload() or {}).get("api", {}).get("history") or {},
            api["key"].get("usage_daily") or 0.0,
        )
        payload = {"ts": time.time(), "stale": False, "api": api}
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
    all_time = credits.get("total_usage") or 0.0
    activity = data.get("activity")

    if activity:
        # Management key: everything is account-wide.
        menu.title(money(all_time), color=spend_color(all_time), sfimage="dollarsign.circle",
                   tooltip=f"OpenRouter account spend today (UTC) · all-time {money(all_time)}")
        if stale:
            dropdown.item(f"Cached — API unreachable ({error or 'offline'})", color=YELLOW, sfimage="clock.arrow.circlepath")
        if activity["today_reqs"]:
            dropdown.item(f"  Today · {activity['today_reqs']} requests · {compact(activity['today_tok'])} tokens", color=DIM)
        else:
            dropdown.item("OpenRouter buckets spend with a delay — today may lag", color=DIM)
        if credits.get("total_credits"):
            total = credits["total_credits"]
            share = min(1.0, max(0.0, all_time / total)) if total > 0 else 0.0
            dropdown.item(f"Credits remaining · {money(max(0.0, total - all_time))}",
                          color=depletion_color(share), progress=share,
                          accessoryWidth='full', accessoryHeight=6)
        dropdown.separator()
        dropdown.item(
            f"Daily spend · last 14 days ({money(sum(activity['daily']))})",
            sparkline=activity["daily"],
            sparklineColor="#0A84FF",
            accessoryWidth=140,
            accessoryHeight=14,
        )
        dropdown.item(f"This week (7 days) · {money(activity['week7'])}")
        if activity.get("week_models"):
            names = [n for n, _ in activity["week_models"]]
            vals = [v for _, v in activity["week_models"]]
            k = min(len(vals), 5)
            dropdown.item("Model mix · 14 days", chart={
                "kind": "stackedbar",
                "values": vals[:k] + ([sum(vals[k:])] if k < len(vals) else []),
                "labels": names[:k] + (["Other"] if k < len(names) else []),
                "colors": [DONUT_COLORS[i % len(DONUT_COLORS)] for i in range(k + (1 if k < len(vals) else 0))],
            }, accessoryWidth="full", accessoryHeight=10)
        if activity["models"]:
            names = [m[0] for m in activity["models"]]
            costs = [m[1] for m in activity["models"]]
            dropdown.item("Today by model", chart=donut(names, costs),
                          accessoryWidth=48, accessoryHeight=48)
            for name, cost, requests in activity["models"]:
                dropdown.submenu(f"  {money(cost)}  {name}").item(
                    f"{requests} request{'s' if requests != 1 else ''} today (UTC)", color=DIM)
        dropdown.separator()
        dropdown.item(f"Account all-time · {money(all_time)}", color=DIM)
        menu.print()
        return

def render_unavailable(reason, hint=None):
    menu = JSONMenu()
    menu.title("$—", color=DIM, sfimage="dollarsign.circle", tooltip="OpenRouter Cost")
    menu.dropdown.item(reason, color=DIM)
    if hint:
        menu.dropdown.item(hint, color=DIM, size=11)
    menu.print()


def main():
    token = resolve_token()
    if not token:
        render_unavailable("No OpenRouter token configured",
                           "Set OPENROUTER_API_TOKEN in this plugin's Settings.")
        return
    try:
        payload = openrouter_payload(token)
    except Exception as e:  # noqa: BLE001 — surface any failure explicitly
        render_unavailable(f"OpenRouter API failed: {e}",
                           "Check the token in this plugin's Settings.")
        return
    render_api(payload["api"], payload.get("stale", False), payload.get("error"))


if __name__ == "__main__":
    main()
