#!/usr/bin/env python3
#
# battery.5m.py — battery charge, health, and power-adapter details.
#
# What it touches: the built-in `pmset` and `system_profiler` tools to read
# power state (no network, no secrets, no writes). `system_profiler
# SPPowerDataType` is the one genuinely slow call here, so it is invoked
# exactly ONCE, with `-detailLevel mini` to keep its own output small, and
# behind a timeout — `subprocess.run(..., timeout=...)` degrades to whatever
# partial output was captured before the kill rather than hanging, so no
# hand-rolled bash timeout wrapper (and its mktemp scratch file) is needed
# here.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>Battery</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Battery charge, time remaining, health, and adapter details, with a desktop widget gauge.</vee.desc>
# <vee.dependencies>python3,pmset,system_profiler</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/battery.png</vee.image>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>pmset,system_profiler,open</vee.exec>
#
# Renders a rich card on the desktop/Notification Center widget surface too.
# <vee.surface>both</vee.surface>

import os
import re
import subprocess

from vee import JSONMenu, Gauge, Stat

TARGET = os.environ.get("VEE_TARGET", "menu")


def run(cmd, timeout):
    """Runs `cmd`, degrading to empty output on any failure/timeout — mirrors
    the bash original's `2>/dev/null` + `set -uo pipefail` (no `-e`): a
    missing/slow tool must degrade, not abort."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


def run_partial(cmd, timeout):
    """Like `run`, but on timeout returns whatever partial stdout was
    captured before the kill — the direct replacement for the bash original's
    hand-rolled `run_with_timeout`, which returned partial output rather than
    nothing."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return out or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# pmset: fast, gives live charge %, plugged/charging state, and the
# time-remaining/time-to-full estimate.
# ---------------------------------------------------------------------------
batt_out = run(["pmset", "-g", "batt"], timeout=3)
batt_lines = batt_out.splitlines()
batt_line = batt_lines[-1] if batt_lines else ""

if "InternalBattery" not in batt_line:
    # Desktop Mac (or a laptop pmset couldn't find a battery for) — one clean
    # row, never a crash or an empty menu.
    if TARGET == "widget":
        Stat(title="Battery", symbol="powerplug.fill", value="—", caption="No battery", status="ok").print()
    else:
        menu = JSONMenu()
        menu.title("", sfimage="powerplug.fill", color="gray")
        menu.dropdown.item("No battery (desktop Mac)", color="gray")
        menu.print()
    raise SystemExit

pct_match = re.search(r"(\d+)%", batt_line)
pct = int(pct_match.group(1)) if pct_match else 0
plugged = "AC attached" in batt_line
charging = re.search(r"(^|; )charging(;| )", batt_line) is not None
fully = re.search(r"(^|; )charged(;| )", batt_line) is not None
remaining_match = re.search(r"(\d+:\d+) remaining", batt_line)
remaining = remaining_match.group(1) if remaining_match else ""
low_power = re.search(r"lowpowermode\s+1", run(["pmset", "-g"], timeout=3)) is not None

# ---------------------------------------------------------------------------
# system_profiler — the one slow call, made exactly once. `-detailLevel mini`
# trims it to model/charge/health/adapter info (no per-cell diagnostics).
# ---------------------------------------------------------------------------
sp_out = run_partial(
    ["system_profiler", "SPPowerDataType", "-detailLevel", "mini"], timeout=2.5
)
sp_lines = sp_out.splitlines()


def first_field(pattern, lines):
    """The value after the first `": "` on the first line containing
    `pattern` — mirrors `awk -F': ' '/pattern/ {print $2}' | head -1`."""
    for line in lines:
        if pattern in line:
            _, sep, value = line.partition(": ")
            return value.strip() if sep else None
    return None


def first_anchored_field(pattern, lines):
    """Like `first_field`, but only lines starting with optional spaces then
    `pattern` — mirrors `awk -F': ' '/^ *pattern/ {print $2}' | head -1`."""
    for line in lines:
        if line.lstrip(" ").startswith(pattern):
            _, sep, value = line.partition(": ")
            return value.strip() if sep else None
    return None


def first_int(pattern, lines):
    """The first run of digits on the first line containing `pattern` —
    mirrors `grep "pattern" | grep -oE '[0-9]+' | head -1`."""
    for line in lines:
        if pattern in line:
            m = re.search(r"\d+", line)
            if m:
                return m.group(0)
    return None


cycle_count = first_field("Cycle Count", sp_lines) or "n/a"
condition = first_field("Condition", sp_lines) or "n/a"
max_capacity = first_int("Maximum Capacity", sp_lines) or ""
adapter_name = first_anchored_field("Name:", sp_lines)
adapter_watts = first_field("Wattage", sp_lines)

adapter_connected = None
in_ac_section = False
for line in sp_lines:
    if "AC Charger Information" in line:
        in_ac_section = True
    if in_ac_section and "Connected" in line:
        _, sep, value = line.partition(": ")
        adapter_connected = value.strip() if sep else None
        break

# Severity color by charge level (mirrors macOS's own low-battery warnings).
if pct <= 20 and not plugged:
    color, status = "red", "warning"
elif pct <= 40:
    color, status = "yellow", "ok"
else:
    color, status = "green", "ok"
if condition != "Normal" and condition != "n/a":
    status = "warning"

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

if TARGET == "widget":
    Gauge(
        title="Battery",
        symbol=symbol,
        tint=color,
        value=f"{pct}%",
        caption=state_text,
        detail=time_text or f"{condition} · {cycle_count} cycles",
        status=status,
        progress=max(0.0, min(pct / 100.0, 1.0)),
        actions=[{"kind": "refresh", "label": "Refresh"}],
        refreshAfter=300,
    ).print()
    raise SystemExit

menu = JSONMenu()
menu.title(f"{pct}%", sfimage=symbol, color=color)

d = menu.dropdown
d.item(
    f"Charge: {pct}%",
    color=color,
    progress=max(0.0, min(pct / 100.0, 1.0)),
    accessory_width=140,
    accessory_height=10,
)
d.item(state_text, color="gray")
if time_text:
    d.item(time_text, color="gray")

d.separator()
d.item("Health", header=True)
d.item(f"Cycle count: {cycle_count}")
# Drop the None color the "Condition" row gets when everything is Normal —
# a stray null color is harmless but noisier than leaving it out (the SDK
# already omits a None-valued option, so this is just for readability).
d.item(f"Condition: {condition}", color="orange" if condition not in ("Normal", "n/a") else None)

if max_capacity:
    try:
        max_cap_pct = int(max_capacity)
        d.item(
            f"Maximum capacity: {max_cap_pct}%",
            chart={
                "kind": "donut",
                "values": [max_cap_pct, max(0, 100 - max_cap_pct)],
                "labels": ["Capacity remaining", "Wear"],
                "colors": ["green", "#3C4046"],
            },
            accessory_width=60,
            accessory_height=60,
        )
        # Apple Silicon Macs no longer expose raw design-vs-current mAh via
        # system_profiler — Maximum Capacity (%) *is* that ratio today, so
        # the donut above stands in for the design/current split the brief
        # asks for on Intel-era machines where the raw figures existed.
    except ValueError:
        pass

d.item(f"Low Power Mode: {'On' if low_power else 'Off'}", color="yellow" if low_power else "gray")

if plugged and adapter_name:
    sub = d.submenu("Power adapter", sfimage="powerplug.fill")
    sub.item(f"Name: {adapter_name}")
    sub.item(f"Wattage: {adapter_watts or 'n/a'} W")
    sub.item(f"Connected: {adapter_connected or 'n/a'}")

d.separator()
d.item(
    "Open Battery settings",
    shell="/usr/bin/open",
    params=["x-apple.systempreferences:com.apple.Battery-Settings.extension"],
    sfimage="gearshape",
)
d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

menu.print()
