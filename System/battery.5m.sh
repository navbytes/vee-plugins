#!/usr/bin/env bash
#
# battery.5m.sh — battery charge, health, and power-adapter details.
#
# What it touches: the built-in `pmset` and `system_profiler` tools to read
# power state (no network, no secrets, no writes). `system_profiler
# SPPowerDataType` is the one genuinely slow call here, so it is invoked
# exactly ONCE, with `-detailLevel mini` to keep its own output small, and
# behind a hand-rolled timeout guard (macOS ships no `timeout(1)`).
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>Battery</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Battery charge, time remaining, health, and adapter details, with a desktop widget gauge.</vee.desc>
# <vee.dependencies>bash,python3,pmset,system_profiler</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,filesystem</vee.capabilities>
# <vee.exec>pmset,system_profiler,open,python3</vee.exec>
# <vee.filesystem.write>$TMPDIR (via mktemp, in run_with_timeout — a scratch buffer for system_profiler's output, removed at the end of every run)</vee.filesystem.write>
#
# Renders a rich card on the desktop/Notification Center widget surface too.
# <vee.surface>both</vee.surface>

set -uo pipefail  # no -e: a missing/timed-out system_profiler must degrade, not abort

TARGET="${VEE_TARGET:-menu}"

# ---------------------------------------------------------------------------
# run_with_timeout DECISECONDS cmd [args...] — a portable stand-in for GNU
# `timeout(1)`, which macOS does not ship. Polls every 0.1s; kills and
# returns whatever output was produced past the deadline rather than hanging
# the plugin. Takes tenths of a second so the caller can ask for e.g. 2.5s
# (25) without floating-point bash arithmetic.
# ---------------------------------------------------------------------------
run_with_timeout() {
  local max="$1"; shift
  local out; out=$(mktemp)
  ("$@" >"$out" 2>/dev/null) &
  local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 0.1
    waited=$((waited + 1))
    if [ "$waited" -ge "$max" ]; then
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      cat "$out"
      rm -f "$out"
      return 124
    fi
  done
  wait "$pid" 2>/dev/null
  cat "$out"
  rm -f "$out"
}

# ---------------------------------------------------------------------------
# pmset: fast, gives live charge %, plugged/charging state, and the
# time-remaining/time-to-full estimate.
# ---------------------------------------------------------------------------
batt_line=$(pmset -g batt 2>/dev/null | tail -1)

if ! echo "$batt_line" | grep -q "InternalBattery"; then
  # Desktop Mac (or a laptop pmset couldn't find a battery for) — one clean
  # row, never a crash or an empty menu.
  if [ "$TARGET" = "widget" ]; then
    echo '{"vee_widget":1,"template":"stat","title":"Battery","symbol":"powerplug.fill","value":"—","caption":"No battery","status":"ok"}'
  else
    echo '{"vee":1,"title":[{"text":"","sfimage":"powerplug.fill","color":"gray"}],"items":[{"text":"No battery (desktop Mac)","color":"gray"}]}'
  fi
  exit 0
fi

pct=$(echo "$batt_line" | grep -oE '[0-9]+%' | head -1 | tr -d '%')
pct=${pct:-0}
plugged=false; echo "$batt_line" | grep -q "AC attached" && plugged=true
charging=false; echo "$batt_line" | grep -qE '(^|; )charging(;| )' && charging=true
fully=false; echo "$batt_line" | grep -qE '(^|; )charged(;| )' && fully=true
remaining=$(echo "$batt_line" | grep -oE '[0-9]+:[0-9]+ remaining' | head -1 | sed 's/ remaining$//')
low_power=false; pmset -g 2>/dev/null | grep -qE 'lowpowermode\s+1' && low_power=true

# ---------------------------------------------------------------------------
# system_profiler — the one slow call, made exactly once. `-detailLevel mini`
# trims it to model/charge/health/adapter info (no per-cell diagnostics).
# ---------------------------------------------------------------------------
sp_out=$(run_with_timeout 25 system_profiler SPPowerDataType -detailLevel mini)

cycle_count=$(echo "$sp_out" | awk -F': ' '/Cycle Count/ {print $2}' | head -1)
condition=$(echo "$sp_out" | awk -F': ' '/Condition/ {print $2}' | head -1)
max_capacity=$(echo "$sp_out" | grep "Maximum Capacity" | grep -oE '[0-9]+' | head -1)
adapter_name=$(echo "$sp_out" | awk -F': ' '/^ *Name:/ {print $2}' | head -1)
adapter_watts=$(echo "$sp_out" | awk -F': ' '/Wattage/ {print $2}' | head -1)
adapter_connected=$(echo "$sp_out" | awk -F': ' '/AC Charger Information/{f=1} f && /Connected/ {print $2; exit}')

cycle_count=${cycle_count:-"n/a"}
condition=${condition:-"n/a"}
max_capacity=${max_capacity:-""}

# Severity color by charge level (mirrors macOS's own low-battery warnings).
if [ "$pct" -le 20 ] && [ "$plugged" = false ]; then color="red"; status="warning"
elif [ "$pct" -le 40 ]; then color="yellow"; status="ok"
else color="green"; status="ok"
fi
[ "$condition" != "Normal" ] && [ "$condition" != "n/a" ] && status="warning"

# ---------------------------------------------------------------------------
# Build the JSON with python3 -c — the adapter name/condition strings come
# straight from system_profiler and need proper JSON string escaping.
# ---------------------------------------------------------------------------
PCT="$pct" PLUGGED="$plugged" CHARGING="$charging" FULLY="$fully" REMAINING="$remaining" \
LOW_POWER="$low_power" CYCLE_COUNT="$cycle_count" CONDITION="$condition" MAX_CAPACITY="$max_capacity" \
ADAPTER_NAME="$adapter_name" ADAPTER_WATTS="$adapter_watts" ADAPTER_CONNECTED="$adapter_connected" \
COLOR="$color" STATUS="$status" TARGET="$TARGET" \
python3 <<'PY'
import json, os

pct = int(os.environ["PCT"])
plugged = os.environ["PLUGGED"] == "true"
charging = os.environ["CHARGING"] == "true"
fully = os.environ["FULLY"] == "true"
remaining = os.environ["REMAINING"]
low_power = os.environ["LOW_POWER"] == "true"
cycle_count = os.environ["CYCLE_COUNT"]
condition = os.environ["CONDITION"]
max_capacity = os.environ["MAX_CAPACITY"]
adapter_name = os.environ["ADAPTER_NAME"]
adapter_watts = os.environ["ADAPTER_WATTS"]
adapter_connected = os.environ["ADAPTER_CONNECTED"]
color = os.environ["COLOR"]
status = os.environ["STATUS"]
target = os.environ["TARGET"]

if charging:
    symbol = "battery.100.bolt"
elif pct >= 90:
    symbol = "battery.100"
elif pct >= 60:
    symbol = "battery.75"
elif pct >= 35:
    symbol = "battery.50"
elif pct >= 15:
    symbol = "battery.25"
else:
    symbol = "battery.0"

if fully:
    state_text = "Charged"
elif charging:
    state_text = "Charging"
elif plugged:
    state_text = "Plugged in, not charging"
else:
    state_text = "On battery"

if remaining and remaining != "0:00":
    label = "Time to full" if (charging or plugged) else "Time remaining"
    time_text = f"{label}: {remaining}"
elif charging or (plugged and not fully):
    time_text = "Time to full: calculating…"
elif not plugged and not fully:
    time_text = "Time remaining: calculating…"
else:
    time_text = None

if target == "widget":
    card = {
        "vee_widget": 1,
        "template": "gauge",
        "title": "Battery",
        "symbol": symbol,
        "tint": color,
        "value": f"{pct}%",
        "caption": state_text,
        "detail": time_text or f"{condition} · {cycle_count} cycles",
        "status": status,
        "progress": max(0.0, min(pct / 100.0, 1.0)),
        "actions": [{"kind": "refresh", "label": "Refresh"}],
        "refresh_after": 300,
    }
    print(json.dumps(card))
    raise SystemExit

items = [
    {
        "text": f"Charge: {pct}%",
        "color": color,
        "progress": max(0.0, min(pct / 100.0, 1.0)),
        "accessoryWidth": 140,
        "accessoryHeight": 10,
    },
    {"text": state_text, "color": "gray"},
]
if time_text:
    items.append({"text": time_text, "color": "gray"})

items += [
    {"separator": True},
    {"header": True, "text": "Health"},
    {"text": f"Cycle count: {cycle_count}"},
    {"text": f"Condition: {condition}", "color": "orange" if condition not in ("Normal", "n/a") else None},
]
if max_capacity:
    try:
        max_cap_pct = int(max_capacity)
        items.append({
            "text": f"Maximum capacity: {max_cap_pct}%",
            "chart": {
                "kind": "donut",
                "values": [max_cap_pct, max(0, 100 - max_cap_pct)],
                "labels": ["Capacity remaining", "Wear"],
                "colors": ["green", "#3C4046"],
            },
            "accessoryWidth": 60,
            "accessoryHeight": 60,
        })
        # Apple Silicon Macs no longer expose raw design-vs-current mAh via
        # system_profiler — Maximum Capacity (%) *is* that ratio today, so
        # the donut above stands in for the design/current split the brief
        # asks for on Intel-era machines where the raw figures existed.
    except ValueError:
        pass

items.append({"text": f"Low Power Mode: {'On' if low_power else 'Off'}", "color": "yellow" if low_power else "gray"})

if plugged and adapter_name:
    items.append({
        "text": "Power adapter",
        "sfimage": "powerplug.fill",
        "submenu": [
            {"text": f"Name: {adapter_name}"},
            {"text": f"Wattage: {adapter_watts or 'n/a'} W"},
            {"text": f"Connected: {adapter_connected or 'n/a'}"},
        ],
    })

items += [
    {"separator": True},
    {
        "text": "Open Battery settings",
        "shell": "/usr/bin/open",
        "params": ["x-apple.systempreferences:com.apple.Battery-Settings.extension"],
        "sfimage": "gearshape",
    },
    {"text": "Refresh", "refresh": True, "sfimage": "arrow.clockwise"},
]

# Drop the None color the "Condition" row gets when everything is Normal —
# a stray null color is harmless but noisier than leaving it out.
for it in items:
    if it.get("color") is None and "color" in it:
        del it["color"]

menu = {
    "vee": 1,
    "title": [{"text": f"{pct}%", "sfimage": symbol, "color": color}],
    "items": items,
}
print(json.dumps(menu))
PY
