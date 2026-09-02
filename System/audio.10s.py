#!/usr/bin/env python3
#
# audio.10s.py — output device, volume, and mute, with an interactive
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
# element (see vee.docs plugin-authoring.md#interactive-controls). Handing
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
#
# The `sh -c 'code' _ <value>` idiom above: Vee appends the new control
# value as one more argv element, landing in $1 inside the script. $1 is
# untrusted (it's whatever VEE_CONTROL_VALUE was at click time) and gets
# substituted straight into the AppleScript source text handed to
# osascript -e, so each script validates it with a `case` guard — digits
# only, in range — before it ever reaches osascript, and exits otherwise.
# That closes off AppleScript injection via a value like `1);do shell script"...`.
# The guards use separate `case` clauses rather than `|` alternation. That
# began as a workaround for a `vee lint` false positive on a literal `|`
# inside a shell= value, fixed upstream in vee#137; it stays because the
# expanded form is the clearer one to audit, not because it is required.
# ---------------------------------------------------------------------------
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>Audio</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Output device, volume, and mute, with a live slider and toggle right in the menu.</vee.desc>
# <vee.dependencies>python3,osascript,system_profiler</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/audio.png</vee.image>
#
# Trust declarations (advisory, never enforced): both control rows can
# change system volume/mute state when clicked, so `exec` is declared
# honestly even though nothing runs on a passive refresh.
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>osascript,system_profiler,sh</vee.exec>

import json
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


VOLUME_RE = re.compile(r"output volume:(\d+)")


def run(cmd, timeout):
    """Runs `cmd`, degrading to empty output on any failure/timeout rather
    than raising — mirrors the bash originals' `2>/dev/null` + `set -uo
    pipefail` (no `-e`): a slow/missing tool must degrade, not abort."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Volume + mute: one AppleScript call returns both, e.g.
#   "output volume:8, input volume:42, alert volume:100, output muted:false"
# ---------------------------------------------------------------------------
settings = run(["osascript", "-e", "get volume settings"], timeout=3)
m = VOLUME_RE.search(settings)
volume = int(m.group(1)) if m else 0
muted = "output muted:true" in settings

# ---------------------------------------------------------------------------
# Output devices: `system_profiler SPAudioDataType -json` is fast (well
# under a second on every machine this was tested on), so — unlike the
# brief's fallback clause anticipates — the device submenu stays in rather
# than getting dropped. If this ever turns out slow on some Mac, drop the
# submenu block below and this comment is where to note why.
# ---------------------------------------------------------------------------
audio_json = run(["system_profiler", "SPAudioDataType", "-json"], timeout=4)

devices = []
default_output = None
try:
    data = json.loads(audio_json)
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

volume_script = (
    'case "$1" in *[!0-9]*) exit 1 ;; "") exit 1 ;; esac; '
    'if [ "$1" -gt 100 ]; then exit 1; fi; '
    '/usr/bin/osascript -e "set volume output volume $1"'
)
mute_script = (
    'case "$1" in [01]) ;; *) exit 1 ;; esac; '
    '/usr/bin/osascript -e "set volume output muted ($1 as boolean)"'
)

menu = JSONMenu()
menu.title(f"{device_label} · {volume}%", sfimage=symbol, color="gray" if muted else "blue")

d = menu.dropdown
d.item(
    f"Volume: {volume}%",
    slider={"min": 0, "max": 100, "value": volume},
    shell="/bin/sh",
    params=["-c", volume_script, "_"],
    refresh=True,
    searchable=False,
)
d.item(
    "Mute",
    toggle=muted,
    shell="/bin/sh",
    params=["-c", mute_script, "_"],
    refresh=True,
    searchable=False,
)
d.separator()

if devices:
    sub = d.submenu("Output devices", sfimage="hifispeaker.fill")
    for name in devices:
        sub.item(name, checked=(name == default_output))

d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

menu.print()
