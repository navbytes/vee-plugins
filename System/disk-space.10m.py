#!/usr/bin/env python3
#
# disk-space.10m.py — how full your volumes are, and what's eating the boot
# disk.
#
# What it touches: the built-in `df` and `du` tools to read disk usage (no
# network, no secrets, no writes). The "big folders in ~" submenu shells out
# to `du -sk -x` once per top-level item under $HOME, which can be genuinely
# slow on a large or cloud-synced home directory, so the whole scan runs
# behind a wall-clock budget and degrades to a "scan skipped" row rather
# than stalling the plugin — `subprocess.run(..., timeout=...)` per `du`
# call plus a shared deadline across the loop does the job natively, no
# hand-rolled bash timeout wrapper (and its mktemp scratch file) needed.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>Disk Space</vee.title>
# <vee.version>1.1</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Free space per volume, a boot-volume breakdown, and the biggest top-level folders in your home directory.</vee.desc>
# <vee.dependencies>python3,df,du</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/disk-space.png</vee.image>
#
# <vee.var>string(DISK_INCLUDE=): Extra mount-point prefixes to always list, comma-separated (e.g. /System/Volumes/Update). Leave blank for the sensible default (skips internal APFS system slices, Simulator disk images, and Recovery).</vee.var>
# <vee.var>number(DISK_WARN=85): Percent used at which a volume's row turns red.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,filesystem</vee.capabilities>
# <vee.exec>df,du,open</vee.exec>
# <vee.filesystem.read>~ (top-level folder sizes only)</vee.filesystem.read>

import os
import subprocess
import time

from vee import JSONMenu

WARN = float(os.environ.get("DISK_WARN", "85"))
INCLUDE_PREFIXES = [p.strip() for p in os.environ.get("DISK_INCLUDE", "").split(",") if p.strip()]
HOME = os.environ["HOME"]

SKIP_PREFIXES = ("/Library/Developer/CoreSimulator/",)

# Budget for the whole "big folders in ~" scan (all `du` calls combined), in
# seconds — mirrors the bash original's `run_with_timeout 10 scan_home`
# (deciseconds -> 1.0s).
# A whole-home `du` cannot finish in a menu-bar plugin's patience: ~/Library
# alone measures in tens of seconds on a normal machine. So the budget buys
# what it can and the scan reports what it did NOT reach, rather than
# pretending a partial list is the whole picture. 10s is invisible here — this
# plugin runs on a 10m interval, off the menu-open path.
SCAN_BUDGET_S = 10.0


def run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


def wanted(mount):
    if any(mount.startswith(p) for p in INCLUDE_PREFIXES):
        return True
    if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
        return False  # internal APFS role volume (Preboot, VM, Update, …)
    if mount.startswith(SKIP_PREFIXES):
        return False  # Simulator disk images
    if "recovery" in mount.lower():
        return False
    return True


def human_gb(kb):
    return f"{kb / 1024 / 1024:.1f} GB"


# ---------------------------------------------------------------------------
# Volumes: `df -Pk -l` already limits to local filesystems (devfs and the
# autofs `map` mounts never appear), so what's left to filter out in code is
# the internal APFS role volumes (Preboot/VM/Update/…) macOS mounts under
# /System/Volumes, Recovery, and Xcode's Simulator disk images.
# ---------------------------------------------------------------------------
df_out = run(["df", "-Pk", "-l"], timeout=5)

volumes = []  # (mount, label, size_kb, used_kb, avail_kb, pct)
for line in df_out.splitlines()[1:]:
    # `df -Pk -l`: Filesystem, 1024-blocks, Used, Available, Capacity, Mounted-on
    # (Mounted-on is everything after the 5th field, so it survives spaces).
    parts = line.split(None, 5)
    if len(parts) < 6:
        continue
    _fs, size_kb, used_kb, avail_kb, capacity, mount = parts
    if not wanted(mount):
        continue
    try:
        size_kb, used_kb, avail_kb = float(size_kb), float(used_kb), float(avail_kb)
        pct = int(capacity.rstrip("%"))
    except ValueError:
        continue
    # macOS's real "boot volume" for a user is /System/Volumes/Data (where
    # almost everything actually lives) — the "/" mount is a small, mostly
    # read-only sealed system snapshot that would otherwise show a
    # confusingly low, separate usage figure for the same physical disk.
    label = "/ (boot volume)" if mount == "/System/Volumes/Data" else mount
    if mount == "/System/Volumes/Data":
        volumes.insert(0, (mount, label, size_kb, used_kb, avail_kb, pct))
    else:
        volumes.append((mount, label, size_kb, used_kb, avail_kb, pct))

# Drop a bare "/" once its Data volume is already represented, to avoid
# showing the same physical disk twice with two different percentages.
has_data = any(m == "/System/Volumes/Data" for m, *_ in volumes)
if has_data:
    volumes = [v for v in volumes if v[0] != "/"]

boot = volumes[0] if volumes else None
boot_pct = boot[5] if boot else 0
if boot_pct >= WARN:
    title_color = "red"
elif boot_pct >= WARN - 20:
    title_color = "yellow"
else:
    title_color = "green"

menu = JSONMenu()
menu.title(f"{boot_pct}%", sfimage="internaldrive", color=title_color)
d = menu.dropdown

for mount, label, size_kb, used_kb, avail_kb, pct in volumes:
    color = "red" if pct >= WARN else ("yellow" if pct >= WARN - 20 else "green")
    d.item(
        f"{label}: {human_gb(used_kb)} / {human_gb(size_kb)} ({pct}%)",
        color=color,
        progress=max(0.0, min(pct / 100.0, 1.0)),
        accessory_width=130,
        accessory_height=8,
    )

if boot:
    _mount, label, size_kb, used_kb, avail_kb, pct = boot
    d.item(
        f"{label} breakdown",
        # No fast, non-scanning macOS CLI exposes "purgeable" bytes
        # directly (diskutil's plist output doesn't carry it on current
        # macOS), so this is Used vs Free rather than a three-way split —
        # add a Purgeable segment if a reliable source shows up.
        chart={
            "kind": "donut",
            "values": [used_kb, avail_kb],
            "labels": ["Used", "Free"],
            "colors": ["orange", "#3C4046"],
        },
        accessory_width=60,
        accessory_height=60,
    )

d.separator()


# ---------------------------------------------------------------------------
# Big folders in ~: one `du -sk -x` per top-level, non-hidden entry — that's
# the "depth 1" bound (each call still has to walk inside that one folder to
# total it, which is unavoidable for an accurate size). The whole loop runs
# behind SCAN_BUDGET_S; a `du` still running past the deadline is left to
# finish on its own rather than hunted down — ponytail: good enough for a
# menu-bar plugin, not a process supervisor.
# ---------------------------------------------------------------------------
def scan_home(home, budget_s):
    """Returns (rows, unmeasured) where rows is [(kb, name), ...] for every
    top-level, non-hidden entry measured within budget_s, and unmeasured is
    the names the budget didn't reach.

    Partial results are kept. The bash original discarded everything on a
    timeout, which on any real machine meant discarding everything, always:
    one slow entry (~/Library) exhausts the budget and took the folders
    already measured down with it, leaving a submenu that never showed a
    single row. Reporting what was measured plus what wasn't is both more
    useful and more honest than a bare "skipped" — a partial list without
    that caveat would imply the largest folder is on it, when the one that
    timed out is usually the largest."""
    deadline = time.monotonic() + budget_s
    try:
        entries = sorted(e for e in os.listdir(home) if not e.startswith("."))
    except OSError:
        entries = []
    rows = []
    unmeasured = []
    for name in entries:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            unmeasured.append(name)
            continue
        path = os.path.join(home, name)
        if not os.path.exists(path):
            continue  # mirrors `[ -e "$entry" ] || continue`
        try:
            r = subprocess.run(["du", "-sk", "-x", path], capture_output=True, text=True, timeout=remaining)
        except subprocess.TimeoutExpired:
            unmeasured.append(name)
            continue
        out = r.stdout.strip()
        if r.returncode != 0 or not out:
            continue
        kb_str, _, _ = out.partition("\t")
        try:
            kb = float(kb_str)
        except ValueError:
            continue
        rows.append((kb, name))
    return rows, unmeasured


sizes, unmeasured = scan_home(HOME, SCAN_BUDGET_S)

folder_rows = []
if sizes:
    sizes.sort(reverse=True)
    for kb, name in sizes[:8]:
        folder_rows.append({"text": f"{name}: {human_gb(kb)}"})

sub = d.submenu("Big folders in ~", sfimage="folder")
if folder_rows:
    for row in folder_rows:
        sub.item(row["text"])
if unmeasured:
    if folder_rows:
        sub.separator()
    # Named, not just counted: "3 folders not measured" invites the reader to
    # trust the list above it, and the folder that ran out of budget is
    # typically the biggest one in ~.
    shown = ", ".join(unmeasured[:4])
    if len(unmeasured) > 4:
        shown += f", +{len(unmeasured) - 4} more"
    sub.item(f"Not measured (scan hit {int(SCAN_BUDGET_S)}s): {shown}", color="gray")
elif not folder_rows:
    sub.item("Nothing to measure in ~", color="gray")

d.separator()
d.item(
    "Open Storage settings",
    shell="/usr/bin/open",
    params=["x-apple.systempreferences:com.apple.settings.Storage"],
    sfimage="internaldrive",
)
d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

menu.print()
