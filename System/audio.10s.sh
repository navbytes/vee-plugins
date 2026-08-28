#!/usr/bin/env bash
#
# audio.10s.sh — output device, volume, and mute, with an interactive
# volume slider and mute toggle right in the dropdown.
#
# What it touches: `osascript` to read and set the system volume/mute state,
# and `system_profiler SPAudioDataType` to list audio devices. No network,
# no secrets, no files written.
#
# ---------------------------------------------------------------------------
# The interactive-control wiring (the part worth reading closely)
#
# A `slider`/`toggle` row's `shell`+`params` command is re-invoked when the
# control changes, with the new value available two ways: the
# `VEE_CONTROL_VALUE` env var, and appended as the command's FINAL argv
# element (see <vee.docs>plugin-authoring.md#interactive-controls). Handing
# `shell` straight to `/usr/bin/osascript` cannot use that appended value —
# `-e "set volume output volume "` has nowhere for a trailing argv element to
# land inside the AppleScript source string (see the "Interactive controls"
# section of the plugin-authoring reference). The fix is the classic
# `sh -c 'code using $1' _ value` idiom: point `shell` at `/bin/sh`, and make
# `params` a `-c SCRIPT _` triple. Vee appends the control's value as one
# more argv element after `params`, so the real invocation becomes
# `/bin/sh -c SCRIPT _ <value>` — inside SCRIPT that value is `$1`.
#
# Verified by hand before wiring it up (Vee can't be driven from here):
#   $ /bin/sh -c '/usr/bin/osascript -e "set volume output volume $1"' _ 42
#   $ osascript -e "output volume of (get volume settings)"   # -> 42
#   $ /bin/sh -c '/usr/bin/osascript -e "set volume output volume $1"' _ 8
#   $ osascript -e "output volume of (get volume settings)"   # -> 8 (restored)
# Toggles pass "1"/"0" (not true/false); AppleScript coerces that directly —
# `1 as boolean` is `true` — so the mute row's script needs no translation.
# ---------------------------------------------------------------------------
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <xbar.title>Audio</xbar.title>
# <xbar.version>1.0</xbar.version>
# <xbar.author>Naveen Kumar</xbar.author>
# <xbar.author.github>navbytes</xbar.author.github>
# <xbar.desc>Output device, volume, and mute, with a live slider and toggle right in the menu.</xbar.desc>
# <xbar.dependencies>bash,python3,osascript,system_profiler</xbar.dependencies>
# <xbar.abouturl>https://github.com/navbytes/vee-plugins</xbar.abouturl>
#
# Trust declarations (advisory, never enforced): both control rows can
# change system volume/mute state when clicked, so `exec` is declared
# honestly even though nothing runs on a passive refresh.
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>osascript,system_profiler,sh,python3</vee.exec>

set -uo pipefail  # no -e: a slow/missing system_profiler must degrade, not abort

# ---------------------------------------------------------------------------
# Volume + mute: one AppleScript call returns both, e.g.
#   "output volume:8, input volume:42, alert volume:100, output muted:false"
# ---------------------------------------------------------------------------
settings=$(osascript -e "get volume settings" 2>/dev/null)
volume=$(echo "$settings" | grep -oE 'output volume:[0-9]+' | grep -oE '[0-9]+')
volume=${volume:-0}
muted=false
echo "$settings" | grep -q "output muted:true" && muted=true

# ---------------------------------------------------------------------------
# Output devices: `system_profiler SPAudioDataType -json` is fast (well
# under a second on every machine this was tested on), so — unlike the
# brief's fallback clause anticipates — the device submenu stays in rather
# than getting dropped. If this ever turns out slow on some Mac, drop the
# submenu block below and this comment is where to note why.
# ---------------------------------------------------------------------------
audio_json=$(system_profiler SPAudioDataType -json 2>/dev/null)

VOLUME="$volume" MUTED="$muted" AUDIO_JSON="$audio_json" python3 <<'PY'
import json, os

volume = int(os.environ["VOLUME"])
muted = os.environ["MUTED"] == "true"

devices = []
default_output = None
try:
    data = json.loads(os.environ["AUDIO_JSON"])
    for entry in data.get("SPAudioDataType", []):
        for dev in entry.get("_items", []):
            if "coreaudio_device_output" not in dev:
                continue  # input-only (e.g. a microphone) — not an output device
            name = dev.get("_name", "Unknown device")
            devices.append(name)
            if dev.get("coreaudio_default_audio_output_device") == "spaudio_yes":
                default_output = name
except (json.JSONDecodeError, AttributeError):
    pass

device_label = default_output or "Unknown output"

if muted:
    symbol = "speaker.slash.fill"
elif volume == 0:
    symbol = "speaker.fill"
elif volume < 34:
    symbol = "speaker.wave.1.fill"
elif volume < 67:
    symbol = "speaker.wave.2.fill"
else:
    symbol = "speaker.wave.3.fill"

# The sh -c 'code' _ <value> idiom described above: Vee appends the new
# control value as one more argv element, landing in $1 inside the script.
# $1 is untrusted (it's whatever VEE_CONTROL_VALUE was at click time) and
# gets substituted straight into the AppleScript source text handed to
# osascript -e, so each script validates it with a `case` guard — digits
# only, in range — before it ever reaches osascript, and exits otherwise.
# That closes off AppleScript injection via a value like `1);do shell script"...`.
# No `|` anywhere in these scripts on purpose — a literal `|` inside a
# shell= command trips vee lint's text-protocol param scanner (see the
# same note in worldclock.1m.py), so the guards use separate `case`
# clauses and an `if` instead of `|`/`||`.
volume_script = (
    'case "$1" in *[!0-9]*) exit 1 ;; "") exit 1 ;; esac; '
    'if [ "$1" -gt 100 ]; then exit 1; fi; '
    '/usr/bin/osascript -e "set volume output volume $1"'
)
mute_script = (
    'case "$1" in [01]) ;; *) exit 1 ;; esac; '
    '/usr/bin/osascript -e "set volume output muted ($1 as boolean)"'
)

items = [
    {
        "text": f"Volume: {volume}%",
        "slider": {"min": 0, "max": 100, "value": volume},
        "shell": "/bin/sh",
        "params": ["-c", volume_script, "_"],
        "refresh": True,
        "searchable": False,
    },
    {
        "text": "Mute",
        "toggle": muted,
        "shell": "/bin/sh",
        "params": ["-c", mute_script, "_"],
        "refresh": True,
        "searchable": False,
    },
    {"separator": True},
]

if devices:
    items.append({
        "text": "Output devices",
        "sfimage": "hifispeaker.fill",
        "submenu": [
            {"text": name, "checked": name == default_output}
            for name in devices
        ],
    })

items.append({"text": "Refresh", "refresh": True, "sfimage": "arrow.clockwise"})

menu = {
    "vee": 1,
    "title": [{"text": f"{device_label} · {volume}%", "sfimage": symbol, "color": "gray" if muted else "blue"}],
    "items": items,
}
print(json.dumps(menu))
PY
