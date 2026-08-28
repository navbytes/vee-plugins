#!/usr/bin/env bash
#
# system-vitals.10s.sh — CPU load, memory pressure, swap, and uptime, with a
# persisted CPU-history sparkline and a rich desktop-widget card.
#
# What it touches: the built-in `top`, `ps`, `memory_pressure`, `sysctl`, and
# `uptime` tools to read system state (no network, no secrets), and its own
# cache file to remember recent CPU samples between runs (every run of a Vee
# plugin is a fresh process, so history has to live on disk). The "Open
# Activity Monitor" row shells out to `open`.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>System Vitals</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>CPU load, memory pressure, swap, and uptime, with a live history sparkline and a desktop widget.</vee.desc>
# <vee.dependencies>bash,python3,top,ps,memory_pressure,sysctl,uptime</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# Warn/critical thresholds (percent CPU used), user-editable in Settings.
# <vee.var>number(VITALS_WARN=70): CPU% at which the title turns yellow.</vee.var>
# <vee.var>number(VITALS_CRIT=90): CPU% at which the title turns red.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,filesystem</vee.capabilities>
# <vee.exec>top,ps,memory_pressure,sysctl,uptime,open,python3</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/cpu-history</vee.filesystem.write>
#
# Renders a rich card on the desktop/Notification Center widget surface too.
# <vee.surface>both</vee.surface>

set -euo pipefail

WARN="${VITALS_WARN:-70}"
CRIT="${VITALS_CRIT:-90}"
TARGET="${VEE_TARGET:-menu}"

# Cache dir: Vee's per-plugin cache path, falling back to TMPDIR/tmp so the
# plugin still works when run outside Vee (e.g. `vee lint`, a bare shell).
CACHE_DIR="${SWIFTBAR_PLUGIN_CACHE_PATH:-${TMPDIR:-/tmp}}"
mkdir -p "$CACHE_DIR" 2>/dev/null || true
HISTORY_FILE="$CACHE_DIR/cpu-history"

# ---------------------------------------------------------------------------
# Gather system state. Every call here is a single, bounded invocation —
# no loops, no unbounded output — to stay well under Vee's execution budget.
# ---------------------------------------------------------------------------

# CPU usage: `top -l 1 -n 0` prints one summary sample with no per-process
# rows, which keeps it fast. usage% = 100 - idle%.
cpu_pct=$(top -l 1 -n 0 2>/dev/null | awk -F'[ %]+' '/CPU usage/ { for (i=1;i<=NF;i++) if ($i=="idle") print 100-$(i-1) }')
cpu_pct=${cpu_pct%.*}
cpu_pct=${cpu_pct:-0}

# Memory pressure: memory_pressure with no allocation flags just samples and
# prints once (it does not "wait forever" unless given -w/-l), so it's safe
# without a timeout wrapper. Turn "free %" into a "pressure %" for the bar.
mem_free_pct=$(memory_pressure 2>/dev/null | awk -F'[ %]+' '/free percentage/ { print $(NF-1) }')
mem_free_pct=${mem_free_pct:-100}
mem_pressure_pct=$((100 - mem_free_pct))

# Swap: `sysctl vm.swapusage` reports "total = X.XXM used = Y.YYM ...".
swap_line=$(sysctl -n vm.swapusage 2>/dev/null || echo "total = 0.00M used = 0.00M free = 0.00M")
swap_total_mb=$(echo "$swap_line" | awk '{print $3}' | tr -d 'M')
swap_used_mb=$(echo "$swap_line" | awk '{print $6}' | tr -d 'M')
swap_total_mb=${swap_total_mb:-0}
swap_used_mb=${swap_used_mb:-0}

uptime_str=$(uptime | sed -E 's/^.*up +//; s/, *[0-9]+ users?,.*$//; s/, *load averages:.*$//')

# ---------------------------------------------------------------------------
# CPU history: append this sample, keep the last 40. Only the canonical
# "menu" run appends — the widget run (same 10s cadence, invoked separately
# under <vee.surface>both</vee.surface>) just reads, so two runs per interval
# don't double up the series.
# ---------------------------------------------------------------------------
if [ "$TARGET" != "widget" ]; then
  { [ -f "$HISTORY_FILE" ] && cat "$HISTORY_FILE"; echo "$cpu_pct"; } | tail -n 40 > "${HISTORY_FILE}.tmp" 2>/dev/null \
    && mv "${HISTORY_FILE}.tmp" "$HISTORY_FILE"
fi
history_csv=""
[ -f "$HISTORY_FILE" ] && history_csv=$(tr '\n' ',' < "$HISTORY_FILE" | sed 's/,$//')

# Severity color, shared by the menu title and the widget's status.
if [ "$cpu_pct" -ge "$CRIT" ]; then color="red"; status="error"
elif [ "$cpu_pct" -ge "$WARN" ]; then color="yellow"; status="warning"
else color="green"; status="ok"
fi

# Top 5 processes by CPU and by memory. `-c` shows the short command name
# (no args) so long invocations don't blow out the row width. `head` closing
# early after 5 lines can SIGPIPE the still-writing `ps`/`tail`; under
# `pipefail` that would trip `set -e`, so the `|| true` swallows just that.
top_cpu=$(ps -Aceo pcpu,comm -r 2>/dev/null | tail -n +2 | head -5) || true
top_mem=$(ps -Aceo pmem,comm -m 2>/dev/null | tail -n +2 | head -5) || true

# ---------------------------------------------------------------------------
# Build the JSON with python3 -c, never by hand-interpolating shell strings
# into the JSON — process names can contain quotes/backslashes, and this is
# the one call in the whole plugin that has to escape them correctly.
# ---------------------------------------------------------------------------
CPU_PCT="$cpu_pct" MEM_PRESSURE_PCT="$mem_pressure_pct" SWAP_USED_MB="$swap_used_mb" \
SWAP_TOTAL_MB="$swap_total_mb" UPTIME_STR="$uptime_str" COLOR="$color" STATUS="$status" \
HISTORY_CSV="$history_csv" TARGET="$TARGET" \
python3 - "$top_cpu" "$top_mem" <<'PY'
import json, math, os, sys

cpu_pct = float(os.environ["CPU_PCT"])
mem_pressure_pct = float(os.environ["MEM_PRESSURE_PCT"])
swap_used = float(os.environ["SWAP_USED_MB"])
swap_total = float(os.environ["SWAP_TOTAL_MB"])
uptime_str = os.environ["UPTIME_STR"]
color = os.environ["COLOR"]
status = os.environ["STATUS"]
target = os.environ["TARGET"]
history = []
for x in os.environ["HISTORY_CSV"].split(","):
    x = x.strip()
    if not x:
        continue
    try:
        v = float(x)
    except ValueError:
        continue  # a corrupt/hand-edited history file degrades to a shorter series, not a crash
    if math.isfinite(v):  # float() also accepts "nan"/"inf" text, which isn't valid JSON output
        history.append(v)

def parse_procs(block):
    procs = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        pct_str, _, name = line.partition(" ")
        try:
            pct = float(pct_str)
        except ValueError:
            continue
        procs.append((name.strip(), pct))
    return procs

top_cpu = parse_procs(sys.argv[1])
top_mem = parse_procs(sys.argv[2])

if target == "widget":
    card = {
        "vee_widget": 1,
        "template": "trend",
        "title": "System Vitals",
        "symbol": "cpu",
        "tint": color,
        "value": f"{cpu_pct:.0f}%",
        "caption": "CPU",
        "detail": f"Mem {mem_pressure_pct:.0f}% · up {uptime_str}",
        "status": status,
        "trend": history,
        "actions": [{"kind": "refresh", "label": "Refresh"}],
        "refresh_after": 10,
    }
    print(json.dumps(card))
    raise SystemExit

def proc_rows(procs):
    rows = []
    for name, pct in procs:
        rows.append({
            "text": f"{name}  {pct:.1f}%",
            "progress": max(0.0, min(pct / 100.0, 1.0)),
            "color": "gray",
            "accessoryWidth": 80,
            "accessoryHeight": 6,
        })
    return rows

swap_frac = 0.0 if swap_total <= 0 else max(0.0, min(swap_used / swap_total, 1.0))
swap_text = "Swap: not in use" if swap_total <= 0 else f"Swap: {swap_used:.0f} / {swap_total:.0f} MB"

items = [
    {"header": True, "text": "Vitals"},
    {
        "text": f"CPU: {cpu_pct:.0f}%",
        "color": color,
        "progress": max(0.0, min(cpu_pct / 100.0, 1.0)),
        "accessoryWidth": 120,
        "accessoryHeight": 8,
    },
    {
        "text": f"Memory pressure: {mem_pressure_pct:.0f}%",
        "color": "orange" if mem_pressure_pct >= 70 else "teal",
        "progress": max(0.0, min(mem_pressure_pct / 100.0, 1.0)),
        "accessoryWidth": 120,
        "accessoryHeight": 8,
    },
    {
        "text": swap_text,
        "color": "purple",
        "progress": swap_frac,
        "accessoryWidth": 120,
        "accessoryHeight": 8,
    },
    {
        "text": "CPU history",
        "sparkline": history,
        "sparklineColor": color,
        "accessoryWidth": 140,
        "accessoryHeight": 20,
    },
    {"text": f"Uptime: {uptime_str}", "color": "gray"},
    {"separator": True},
    {"header": True, "text": "Top CPU"},
    {"text": "Top 5 processes", "sfimage": "cpu", "submenu": proc_rows(top_cpu)},
    {"header": True, "text": "Top Memory"},
    {"text": "Top 5 processes", "sfimage": "memorychip", "submenu": proc_rows(top_mem)},
    {"separator": True},
    {
        "text": "Open Activity Monitor",
        "shell": "/usr/bin/open",
        "params": ["-a", "Activity Monitor"],
        "sfimage": "gauge.with.dots.needle.67percent",
    },
    {"text": "Refresh", "refresh": True, "sfimage": "arrow.clockwise"},
]

menu = {
    "vee": 1,
    "title": [{"text": f"{cpu_pct:.0f}%", "color": color, "sfimage": "cpu"}],
    "items": items,
}
print(json.dumps(menu))
PY
