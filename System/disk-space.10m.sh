#!/usr/bin/env bash
#
# disk-space.10m.sh — how full your volumes are, and what's eating the boot
# disk.
#
# What it touches: the built-in `df` and `du` tools to read disk usage (no
# network, no secrets, no writes). The "big folders in ~" submenu shells out
# to `du -sk -x` once per top-level item under $HOME, which can be genuinely
# slow on a large or cloud-synced home directory, so the whole scan runs
# behind a hand-rolled timeout guard (macOS ships no `timeout(1)`) and
# degrades to a "scan skipped" row rather than stalling the plugin.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <xbar.title>Disk Space</xbar.title>
# <xbar.version>1.0</xbar.version>
# <xbar.author>Naveen Kumar</xbar.author>
# <xbar.author.github>navbytes</xbar.author.github>
# <xbar.desc>Free space per volume, a boot-volume breakdown, and the biggest top-level folders in your home directory.</xbar.desc>
# <xbar.dependencies>bash,python3,df,du</xbar.dependencies>
# <xbar.abouturl>https://github.com/navbytes/vee-plugins</xbar.abouturl>
#
# <xbar.var>string(DISK_INCLUDE=): Extra mount-point prefixes to always list, comma-separated (e.g. /System/Volumes/Update). Leave blank for the sensible default (skips internal APFS system slices, Simulator disk images, and Recovery).</xbar.var>
# <xbar.var>number(DISK_WARN=85): Percent used at which a volume's row turns red.</xbar.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,filesystem</vee.capabilities>
# <vee.exec>df,du,open,python3</vee.exec>
# <vee.filesystem.read>~ (top-level folder sizes only)</vee.filesystem.read>
# <vee.filesystem.write>$TMPDIR (via mktemp, in run_with_timeout — a scratch buffer for the du scan's output, removed at the end of every run)</vee.filesystem.write>

set -uo pipefail  # no -e: a slow/interrupted du must degrade, not abort

WARN="${DISK_WARN:-85}"
INCLUDE="${DISK_INCLUDE:-}"

# ---------------------------------------------------------------------------
# run_with_timeout DECISECONDS cmd [args...] — a portable stand-in for GNU
# `timeout(1)`. Polls every 0.1s; past the deadline it kills the wrapper
# subshell and returns whatever partial output exists (the caller treats
# that as "didn't finish" and shows a fallback row). Any `du` still running
# at that point is orphaned and left to finish on its own rather than
# hunted down — ponytail: good enough for a menu-bar plugin, not a process
# supervisor.
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
# Volumes: `df -H -l` already limits to local filesystems (devfs and the
# autofs `map` mounts never appear), so what's left to filter out in code is
# the internal APFS role volumes (Preboot/VM/Update/…) macOS mounts under
# /System/Volumes, Recovery, and Xcode's Simulator disk images.
# ---------------------------------------------------------------------------
df_out=$(df -Pk -l 2>/dev/null)

# ---------------------------------------------------------------------------
# Big folders in ~: one `du -sk -x` per top-level, non-hidden entry — that's
# the "depth 1" bound (each call still has to walk inside that one folder to
# total it, which is unavoidable for an accurate size). The whole loop runs
# behind the timeout guard below.
# ---------------------------------------------------------------------------
scan_home() {
  local home="$1"
  for entry in "$home"/*; do
    [ -e "$entry" ] || continue
    du -sk -x "$entry" 2>/dev/null
  done
}
folder_sizes=$(run_with_timeout 10 scan_home "$HOME")
scan_rc=$?

# ---------------------------------------------------------------------------
# Build the JSON with python3 — mount points and folder names can contain
# spaces or quotes, so this is where everything gets escaped correctly.
# ---------------------------------------------------------------------------
DF_OUT="$df_out" HOME_DIR="$HOME" WARN="$WARN" INCLUDE="$INCLUDE" \
FOLDER_SIZES="$folder_sizes" SCAN_RC="$scan_rc" \
python3 <<'PY'
import json, os

df_out = os.environ["DF_OUT"]
home_dir = os.environ["HOME_DIR"]
warn = float(os.environ["WARN"])
include_prefixes = [p.strip() for p in os.environ["INCLUDE"].split(",") if p.strip()]
folder_sizes_raw = os.environ["FOLDER_SIZES"]
scan_rc = int(os.environ["SCAN_RC"])

SKIP_PREFIXES = ("/Library/Developer/CoreSimulator/",)

def wanted(mount):
    if any(mount.startswith(p) for p in include_prefixes):
        return True
    if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
        return False  # internal APFS role volume (Preboot, VM, Update, …)
    if mount.startswith(SKIP_PREFIXES):
        return False  # Simulator disk images
    if "recovery" in mount.lower():
        return False
    return True

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

def human_gb(kb):
    return f"{kb / 1024 / 1024:.1f} GB"

boot = volumes[0] if volumes else None
boot_pct = boot[5] if boot else 0
if boot_pct >= warn:
    title_color = "red"
elif boot_pct >= warn - 20:
    title_color = "yellow"
else:
    title_color = "green"

items = []
for mount, label, size_kb, used_kb, avail_kb, pct in volumes:
    color = "red" if pct >= warn else ("yellow" if pct >= warn - 20 else "green")
    items.append({
        "text": f"{label}: {human_gb(used_kb)} / {human_gb(size_kb)} ({pct}%)",
        "color": color,
        "progress": max(0.0, min(pct / 100.0, 1.0)),
        "accessoryWidth": 130,
        "accessoryHeight": 8,
    })

if boot:
    _mount, label, size_kb, used_kb, avail_kb, pct = boot
    items.append({
        "text": f"{label} breakdown",
        # No fast, non-scanning macOS CLI exposes "purgeable" bytes
        # directly (diskutil's plist output doesn't carry it on current
        # macOS), so this is Used vs Free rather than a three-way split —
        # add a Purgeable segment if a reliable source shows up.
        "chart": {
            "kind": "donut",
            "values": [used_kb, avail_kb],
            "labels": ["Used", "Free"],
            "colors": ["orange", "#3C4046"],
        },
        "accessoryWidth": 60,
        "accessoryHeight": 60,
    })

items.append({"separator": True})

# Big folders in ~
folder_rows = []
if scan_rc == 0 and folder_sizes_raw.strip():
    sizes = []
    for line in folder_sizes_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        kb_str, _, path = line.partition("\t")
        try:
            kb = float(kb_str)
        except ValueError:
            continue
        sizes.append((kb, os.path.basename(path.rstrip("/"))))
    sizes.sort(reverse=True)
    for kb, name in sizes[:8]:
        folder_rows.append({"text": f"{name}: {human_gb(kb)}"})

big_folders_item = {"text": "Big folders in ~", "sfimage": "folder"}
if folder_rows:
    big_folders_item["submenu"] = folder_rows
else:
    big_folders_item["submenu"] = [{"text": "Scan skipped (slow disk)", "color": "gray"}]
items.append(big_folders_item)

items += [
    {"separator": True},
    {
        "text": "Open Storage settings",
        "shell": "/usr/bin/open",
        "params": ["x-apple.systempreferences:com.apple.settings.Storage"],
        "sfimage": "internaldrive",
    },
    {"text": "Refresh", "refresh": True, "sfimage": "arrow.clockwise"},
]

menu = {
    "vee": 1,
    "title": [{"text": f"{boot_pct}%", "sfimage": "internaldrive", "color": title_color}],
    "items": items,
}
print(json.dumps(menu))
PY
