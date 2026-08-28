#!/usr/bin/env python3
#
# uptime.5m.py -- are my things up.
#
# Written in Python 3 (see the shebang -- Apple ships it, no new dependency).
# The ".5m" in the filename only carries Vee's refresh-interval
# convention; the shebang picks the interpreter. Python buys a real thread
# pool for the parallel checks (concurrent.futures, stdlib) and correct JSON
# escaping (json.dumps) for free.
#
# What it does:
#   - Reads UPTIME_TARGETS ("Label=https://url,Label2=https://url2", capped
#     at 12) and checks each with one `curl -o /dev/null -sS -w '%{http_code}
#     %{time_total}' --max-time 6 -L <url>`, all launched concurrently so 12
#     slow targets take ~6s, not 72.
#   - Menu-bar title: "All up" in green, or "N down" in red.
#   - Dropdown: a header per state (Up/Degraded/Down), a row per target with
#     its status/latency (href to the URL), a submenu per target with a
#     sparkline of its last 20 response times and its last-seen error, and a
#     stacked-bar summarising the three counts.
#   - Also serves a "board" widget card (<vee.surface>both</vee.surface>) --
#     one tile per target with a rolled-up ok/warning/error status.
#   - No default targets are shipped -- an empty UPTIME_TARGETS prints a
#     friendly row with an example rather than pinging anyone else's server.
#
# <vee.title>Uptime</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Parallel HTTP health checks for your own endpoints, in the menu bar and as a widget board.</vee.desc>
# <vee.dependencies>python3,curl</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# <vee.var>string(UPTIME_TARGETS=): Comma-separated Label=https://url checks, e.g. API=https://api.example.com/health,Docs=https://docs.example.com. Capped at 12.</vee.var>
#
# This plugin renders both in the menu and as a widget board:
# <vee.surface>both</vee.surface>
#
# Trust declarations (advisory, never enforced -- see docs/trust-model.md):
# <vee.capabilities>network,filesystem,exec</vee.capabilities>
# <vee.network>Whatever hosts appear in UPTIME_TARGETS -- arbitrary, user-supplied HTTP(S) endpoints. This plugin ships with none configured and never contacts anything else.</vee.network>
# <vee.exec>curl</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/uptime-*.json (per-target response-time history and last error)</vee.filesystem.write>

import concurrent.futures
import hashlib
import json
import math
import os
import re
import subprocess
import sys

MAX_TARGETS = 12
CURL_TIMEOUT = 6  # seconds, per-target -- matches the brief's own curl flags
SLOW_THRESHOLD = 1.5  # seconds -- a fast 2xx/3xx below this is "up"
HISTORY_LEN = 20

CACHE_DIR = os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH") or os.environ.get("TMPDIR", "/tmp")
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except OSError:
    pass

VEE_TARGET = os.environ.get("VEE_TARGET", "menu")

STATUS_COLOR = {"up": "green", "degraded": "orange", "down": "red"}


# ---------------------------------------------------------------------------
# Parse UPTIME_TARGETS
# ---------------------------------------------------------------------------

def parse_targets():
    raw = (os.environ.get("UPTIME_TARGETS") or "").strip()
    if not raw:
        return []
    targets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        label, url = entry.split("=", 1)
        label, url = label.strip(), url.strip()
        if not label or not (url.startswith("http://") or url.startswith("https://")):
            continue
        targets.append((label, url))
    return targets[:MAX_TARGETS]


# ---------------------------------------------------------------------------
# One HTTP check
# ---------------------------------------------------------------------------

def check_target(url):
    try:
        r = subprocess.run(
            [
                "/usr/bin/curl", "-o", "/dev/null", "-sS",
                "-w", "%{http_code} %{time_total}",
                "--max-time", str(CURL_TIMEOUT), "-L", url,
            ],
            capture_output=True, text=True, timeout=CURL_TIMEOUT + 3,
        )
        m = re.match(r"^(\d+)\s+([\d.]+)\s*$", r.stdout.strip())
        if not m:
            return {"http_code": 0, "time_total": None, "error": (r.stderr.strip() or "curl produced no output")[:200]}
        code, time_total = int(m.group(1)), float(m.group(2))
        if code and code < 400:
            error = None
        elif code:
            error = r.stderr.strip() or f"HTTP {code}"
        else:
            error = r.stderr.strip() or "connection failed"
        return {"http_code": code, "time_total": time_total, "error": error[:200] if error else None}
    except subprocess.TimeoutExpired:
        return {"http_code": 0, "time_total": None, "error": "timeout"}
    except Exception as e:
        return {"http_code": 0, "time_total": None, "error": str(e)[:200]}


def classify(result):
    code, time_total = result["http_code"], result["time_total"]
    if code == 0 or code >= 400:
        return "down"
    if (time_total is not None and time_total >= SLOW_THRESHOLD) or 300 <= code < 400:
        return "degraded"
    return "up"


def run_checks(targets):
    """All checks launched concurrently -- a hard cap in wall time comes from
    each curl's own --max-time, not from serializing the list."""
    results = {}
    if not targets:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        future_to_url = {pool.submit(check_target, url): url for _, url in targets}
        for future in concurrent.futures.as_completed(future_to_url, timeout=CURL_TIMEOUT + 5):
            url = future_to_url[future]
            try:
                results[url] = future.result()
            except Exception as e:
                results[url] = {"http_code": 0, "time_total": None, "error": str(e)[:200]}
    return results


# ---------------------------------------------------------------------------
# Per-target history cache (response times + last error)
# ---------------------------------------------------------------------------

def cache_path(label, url):
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:40] or "target"
    return os.path.join(CACHE_DIR, f"uptime-{safe}-{h}.json")


def load_history(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return [], None
    if not isinstance(data, dict):
        return [], None
    # A hand-edited/truncated cache can have "times" as anything JSON allows
    # (a string, a number, null...) — validate its shape here so a bad file
    # degrades to "no history" rather than crashing the later `times.append`.
    times = data.get("times", [])
    if not isinstance(times, list):
        times = []
    # bool is an int subclass and json.load itself accepts bare NaN/Infinity
    # tokens -- excluding both keeps every surviving value a plain finite
    # number, since NaN would otherwise round-trip into invalid JSON output.
    times = [
        t for t in times
        if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
    ]
    last_error = data.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        last_error = None
    return times, last_error


def save_history(path, times, last_error):
    try:
        with open(path, "w") as f:
            json.dump({"times": times[-HISTORY_LEN:], "last_error": last_error}, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Gather everything (shared by both the menu and widget outputs)
# ---------------------------------------------------------------------------

targets = parse_targets()
checks = run_checks(targets)

rows = []  # one dict per target: label, url, result, status, times, last_error
for label, url in targets:
    result = checks.get(url) or {"http_code": 0, "time_total": None, "error": "not checked"}
    status = classify(result)
    path = cache_path(label, url)
    times, last_error = load_history(path)
    if result["time_total"] is not None and result["http_code"]:
        times.append(round(result["time_total"], 3))
        times = times[-HISTORY_LEN:]
    if result["error"]:
        last_error = result["error"]
    save_history(path, times, last_error)
    rows.append({
        "label": label, "url": url, "result": result, "status": status,
        "times": times, "last_error": last_error,
    })

up = [r for r in rows if r["status"] == "up"]
degraded = [r for r in rows if r["status"] == "degraded"]
down = [r for r in rows if r["status"] == "down"]

if down:
    widget_status = "error"
elif degraded:
    widget_status = "warning"
else:
    widget_status = "ok"


def status_text(row):
    result = row["result"]
    if result["http_code"] == 0:
        return f"{row['label']} — {result['error'] or 'unreachable'}"
    return f"{row['label']} — {result['http_code']} · {result['time_total']:.2f}s"


# ---------------------------------------------------------------------------
# Widget mode: one JSON widget-card object, nothing else.
# ---------------------------------------------------------------------------

if VEE_TARGET == "widget":
    if not targets:
        card = {
            "vee_widget": 1,
            "template": "stat",
            "title": "Uptime",
            "symbol": "questionmark.circle",
            "tint": "gray",
            "value": "Not configured",
            "caption": "Set UPTIME_TARGETS",
            "status": "warning",
        }
    else:
        card = {
            "vee_widget": 1,
            "template": "board",
            "title": "Uptime",
            "symbol": "checkmark.circle" if not down else "exclamationmark.triangle",
            "tint": "green" if not down else "red",
            "value": "All up" if not down else f"{len(down)} down",
            "caption": f"{len(targets)} target{'s' if len(targets) != 1 else ''}",
            "status": widget_status,
            "items": [
                {
                    "label": row["label"],
                    "value": (
                        f"{row['result']['http_code']} · {row['result']['time_total']:.2f}s"
                        if row["result"]["http_code"] else (row["result"]["error"] or "down")
                    ),
                    "tint": STATUS_COLOR[row["status"]],
                    "url": row["url"],
                }
                for row in rows
            ],
            "actions": [{"kind": "refresh", "label": "Refresh"}],
            "refresh_after": 300,
        }
    print(json.dumps(card))
    sys.stdout.flush()
    raise SystemExit(0)


# ---------------------------------------------------------------------------
# Menu mode
# ---------------------------------------------------------------------------

if not targets:
    title = [{"text": "Not configured", "sfimage": "questionmark.circle", "color": "gray"}]
    items = [
        {"text": "No targets configured yet", "color": "gray"},
        {
            "text": "Set UPTIME_TARGETS, e.g.: API=https://api.example.com/health,Docs=https://docs.example.com",
            "color": "gray",
        },
    ]
else:
    if down:
        title = [{"text": f"{len(down)} down", "sfimage": "exclamationmark.triangle", "color": "red"}]
    else:
        title = [{"text": "All up", "sfimage": "checkmark.circle", "color": "green"}]

    items = []

    def target_row(row):
        submenu = []
        if row["times"]:
            submenu.append({
                "text": f"Last {len(row['times'])} response times",
                "sparkline": row["times"],
                "sparklineColor": STATUS_COLOR[row["status"]],
                "accessoryWidth": 140,
                "accessoryHeight": 20,
                "tooltip": f"Response time history for {row['label']}, in seconds",
            })
        else:
            submenu.append({"text": "No response-time history yet", "color": "gray"})
        submenu.append({
            "text": f"Last error: {row['last_error']}" if row["last_error"] else "No errors recorded",
            "color": "red" if row["last_error"] else "gray",
        })
        return {
            "text": status_text(row),
            "color": STATUS_COLOR[row["status"]],
            "href": row["url"],
            "tooltip": row["url"],
            "submenu": submenu,
        }

    for group_label, group in (("Down", down), ("Degraded", degraded), ("Up", up)):
        if not group:
            continue
        items.append({"header": True, "text": group_label})
        for row in group:
            items.append(target_row(row))
        items.append({"separator": True})

    items.append({
        "text": "Status overview",
        "chart": {
            "kind": "stackedbar",
            "values": [len(up), len(degraded), len(down)],
            "labels": ["Up", "Degraded", "Down"],
            "colors": ["green", "orange", "red"],
        },
        "accessoryWidth": "full",
        "accessoryHeight": 14,
    })
    items.append({"separator": True})

items.append({"text": "Refresh", "refresh": True, "sfimage": "arrow.clockwise"})

print(json.dumps({"vee": 1, "title": title, "items": items}))
sys.stdout.flush()
