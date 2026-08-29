#!/usr/bin/env python3
#
# system-vitals.10s.py — CPU load, memory pressure, swap, and uptime, with a
# persisted CPU-history sparkline and a rich desktop-widget card.
#
# What it touches: the built-in `top`, `ps`, `memory_pressure`, `sysctl`, and
# `uptime` tools to read system state (no network, no secrets), and its own
# cache file to remember recent CPU samples between runs (every run of a Vee
# plugin is a fresh process, so history has to live on disk). The "Open
# Activity Monitor" row shells out to `open`.
#
# The cache file is one integer per line (the bash original's own format —
# `cpu_pct=${cpu_pct%.*}` truncates to an integer string before it's ever
# written), kept to the last 40 samples. Kept byte-compatible here so an
# existing install's history file still reads.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>System Vitals</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>CPU load, memory pressure, swap, and uptime, with a live history sparkline and a desktop widget.</vee.desc>
# <vee.dependencies>python3,top,ps,memory_pressure,sysctl,uptime</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/system-vitals.png</vee.image>
#
# Warn/critical thresholds (percent CPU used), user-editable in Settings.
# <vee.var>number(VITALS_WARN=70): CPU% at which the title turns yellow.</vee.var>
# <vee.var>number(VITALS_CRIT=90): CPU% at which the title turns red.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,filesystem</vee.capabilities>
# <vee.exec>top,ps,memory_pressure,sysctl,uptime,open</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/cpu-history</vee.filesystem.write>
#
# Renders a rich card on the desktop/Notification Center widget surface too.
# <vee.surface>both</vee.surface>

import json
import math
import os
import re
import subprocess


class JSONSection:
    """A dropdown section — see https://vee.navbytes.io/guide/json-output/.
    This plugin builds the JSON output format directly, no dependency."""

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


class WidgetCard(dict):
    """The VEE_TARGET=widget stdout payload — https://vee.navbytes.io/guide/widgets/#the-card."""

    def print(self):
        print(json.dumps(self, ensure_ascii=False))


def Trend(**opts):
    wire_keys = {"refreshAfter": "refresh_after", "staleAfter": "stale_after"}
    card = WidgetCard(vee_widget=1, template="trend")
    for k, v in opts.items():
        if v is not None:
            card[wire_keys.get(k, k)] = v
    return card


WARN = float(os.environ.get("VITALS_WARN", "70"))
CRIT = float(os.environ.get("VITALS_CRIT", "90"))
TARGET = os.environ.get("VEE_TARGET", "menu")

# Cache dir: Vee's per-plugin cache path, falling back to TMPDIR/tmp so the
# plugin still works when run outside Vee (e.g. `vee lint`, a bare shell).
CACHE_DIR = os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH") or os.environ.get("TMPDIR", "/tmp")
os.makedirs(CACHE_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(CACHE_DIR, "cpu-history")


def run(cmd, timeout):
    """Runs `cmd`, degrading to empty output on any failure/timeout — mirrors
    the bash original's `2>/dev/null` (every call here is fast enough that
    `set -euo pipefail` never had to guard it, but a timeout is cheap
    insurance)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Gather system state. Every call here is a single, bounded invocation — no
# loops, no unbounded output — to stay well under Vee's execution budget.
# ---------------------------------------------------------------------------

# CPU usage: `top -l 1 -n 0` prints one summary sample with no per-process
# rows, which keeps it fast. usage% = 100 - idle%.
top_out = run(["top", "-l", "1", "-n", "0"], timeout=5)
idle_match = re.search(r"([\d.]+)%\s*idle", top_out)
cpu_pct = int(100 - float(idle_match.group(1))) if idle_match else 0

# Memory pressure: memory_pressure with no allocation flags just samples and
# prints once (it does not "wait forever" unless given -w/-l), so it's safe
# without a long timeout. Turn "free %" into a "pressure %" for the bar.
mem_out = run(["memory_pressure"], timeout=5)
mem_free_match = re.search(r"free percentage:\s*(\d+)%", mem_out)
mem_free_pct = int(mem_free_match.group(1)) if mem_free_match else 100
mem_pressure_pct = 100 - mem_free_pct

# Swap: `sysctl vm.swapusage` reports "total = X.XXM used = Y.YYM ...".
swap_out = run(["sysctl", "-n", "vm.swapusage"], timeout=3)
swap_fields = swap_out.split()
try:
    swap_total_mb = float(swap_fields[2].rstrip("M"))
    swap_used_mb = float(swap_fields[5].rstrip("M"))
except (IndexError, ValueError):
    swap_total_mb = swap_used_mb = 0.0

uptime_out = run(["uptime"], timeout=3)
uptime_str = re.sub(r"^.*up +", "", uptime_out)
uptime_str = re.sub(r", *\d+ users?,.*$", "", uptime_str)
uptime_str = re.sub(r", *load averages:.*$", "", uptime_str).strip()

# ---------------------------------------------------------------------------
# CPU history: append this sample, keep the last 40. Only the canonical
# "menu" run appends — the widget run (same 10s cadence, invoked separately
# under <vee.surface>both</vee.surface>) just reads, so two runs per interval
# don't double up the series.
# ---------------------------------------------------------------------------
if TARGET != "widget":
    try:
        with open(HISTORY_FILE) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    lines.append(str(cpu_pct))
    lines = lines[-40:]
    try:
        tmp_path = HISTORY_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_path, HISTORY_FILE)
    except OSError:
        pass

history = []
try:
    with open(HISTORY_FILE) as f:
        for x in f.read().strip().split("\n"):
            x = x.strip()
            if not x:
                continue
            try:
                v = float(x)
            except ValueError:
                continue  # a corrupt/hand-edited history file degrades to a shorter series, not a crash
            if math.isfinite(v):  # float() also accepts "nan"/"inf" text, which isn't valid JSON output
                history.append(v)
except OSError:
    pass

# Severity color, shared by the menu title and the widget's status.
if cpu_pct >= CRIT:
    color, status = "red", "error"
elif cpu_pct >= WARN:
    color, status = "yellow", "warning"
else:
    color, status = "green", "ok"

# Top 5 processes by CPU and by memory. The `c` in `-Aceo` shows the short
# command name (no args) so long invocations don't blow out the row width.
top_cpu_out = run(["ps", "-Aceo", "pcpu,comm", "-r"], timeout=5)
top_mem_out = run(["ps", "-Aceo", "pmem,comm", "-m"], timeout=5)


def parse_procs(block):
    procs = []
    for line in block.splitlines()[1:6]:  # skip the header row, take 5
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


top_cpu = parse_procs(top_cpu_out)
top_mem = parse_procs(top_mem_out)

if TARGET == "widget":
    Trend(
        title="System Vitals",
        symbol="cpu",
        tint=color,
        value=f"{cpu_pct:.0f}%",
        caption="CPU",
        detail=f"Mem {mem_pressure_pct:.0f}% · up {uptime_str}",
        status=status,
        trend=history,
        actions=[{"kind": "refresh", "label": "Refresh"}],
        refreshAfter=10,
    ).print()
    raise SystemExit


def proc_rows(section, procs):
    for name, pct in procs:
        section.item(
            f"{name}  {pct:.1f}%",
            progress=max(0.0, min(pct / 100.0, 1.0)),
            color="gray",
            accessoryWidth=80,
            accessoryHeight=6,
        )


swap_frac = 0.0 if swap_total_mb <= 0 else max(0.0, min(swap_used_mb / swap_total_mb, 1.0))
swap_text = "Swap: not in use" if swap_total_mb <= 0 else f"Swap: {swap_used_mb:.0f} / {swap_total_mb:.0f} MB"

menu = JSONMenu()
menu.title(f"{cpu_pct:.0f}%", color=color, sfimage="cpu")

d = menu.dropdown
d.item("Vitals", header=True)
d.item(
    f"CPU: {cpu_pct:.0f}%",
    color=color,
    progress=max(0.0, min(cpu_pct / 100.0, 1.0)),
    accessoryWidth=120,
    accessoryHeight=8,
)
d.item(
    f"Memory pressure: {mem_pressure_pct:.0f}%",
    color="orange" if mem_pressure_pct >= 70 else "teal",
    progress=max(0.0, min(mem_pressure_pct / 100.0, 1.0)),
    accessoryWidth=120,
    accessoryHeight=8,
)
d.item(
    swap_text,
    color="purple",
    progress=swap_frac,
    accessoryWidth=120,
    accessoryHeight=8,
)
d.item(
    "CPU history",
    sparkline=history,
    sparklineColor=color,
    accessoryWidth=140,
    accessoryHeight=20,
)
d.item(f"Uptime: {uptime_str}", color="gray")
d.separator()
d.item("Top CPU", header=True)
sub = d.submenu("Top 5 processes", sfimage="cpu")
proc_rows(sub, top_cpu)
d.item("Top Memory", header=True)
sub = d.submenu("Top 5 processes", sfimage="memorychip")
proc_rows(sub, top_mem)
d.separator()
d.item(
    "Open Activity Monitor",
    shell="/usr/bin/open",
    params=["-a", "Activity Monitor"],
    sfimage="gauge.with.dots.needle.67percent",
)
d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

menu.print()
