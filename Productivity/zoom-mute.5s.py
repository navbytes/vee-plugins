#!/usr/bin/env python3
#
# zoom-mute.5s.py — Zoom's in-meeting mute status in the menu bar, with a
# one-click mute/unmute/join-audio action.
#
# Zoom has no CLI and writes no state file for this, so the only way to read
# its mute status is the same trick the original xbar plugin used: ask
# System Events for the *labels* of Zoom's own "Meeting" menu — "Mute audio"
# vs. "Unmute audio" tells you which state it's in. The click action drives
# that same menu directly: System Events clicks the matching "Mute
# audio"/"Unmute audio" (or "...telephone") item on the zoom.us process
# itself, the same targeting the status read already uses — never a
# simulated keystroke sent to whatever app happens to be frontmost.
#
# macOS will prompt to let the calling process control "System Events" the
# first time this runs (Privacy & Security -> Automation) — reading Zoom's
# menu labels and clicking its menu items both go through that same bridge,
# so Accessibility access may also be asked for. That is macOS's own
# permission model, not something this plugin requests specially; decline it
# and every run degrades to the "Permission needed" row below instead of
# failing silently.
#
# Only reads Zoom's English menu item labels, same limitation as the
# original — needs Zoom's display language set to English.
#
# Ported from xbar's Lifestyle/zoom_mute.3s.sh (Dustin, dustincredible,
# https://github.com/matryer/xbar-plugins/blob/main/Lifestyle/zoom_mute.3s.sh).
# Security audit: CLEAN, no findings — no network, no secrets, nothing
# destructive; every osascript call is a fixed literal, no runtime string
# assembly. Changes made while porting: split the original's single "off"
# state into "Zoom not installed" vs. "not running" vs. "running but no
# meeting" (three distinct, actionable rows instead of one silent blank);
# added a distinct "Automation permission needed" row instead of a
# misleading state when System Events access is denied; renamed the
# AppleScript's return values from Zoom's own true/false/1/off strings to
# named states for the same reason; and, after a later security review,
# swapped the action from a simulated Cmd+Shift+A keystroke (which lands on
# whatever app is frontmost, not necessarily Zoom) for a direct click on the
# same Meeting-menu item the status read already locates. Same detection
# logic as upstream otherwise.
#
# <vee.title>Zoom Mute</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Zoom's in-meeting mute status in the menu bar, with a one-click mute/unmute toggle that drives Zoom's own menu via System Events (Automation permission).</vee.desc>
# <vee.dependencies>python3,osascript</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>osascript</vee.exec>

import os
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
        import json
        payload = {"vee": 1, "title": self._titles}
        if self._items:
            payload["items"] = self._items
        print(json.dumps(payload, ensure_ascii=False))


AMBER = "#F5A623"
DIM = "#8A8F98"
RED = "#FF3B30"
GREEN = "#34C759"

# Static AppleScript literals only — nothing here is built from runtime
# input, so there is no injection surface to guard.
STATUS_SCRIPT = '''
tell application "System Events"
    if (name of processes) contains "zoom.us" then
        tell application process "zoom.us"
            if menu item "Join Audio" of menu 1 of menu bar item "Meeting" of menu bar 1 exists then
                return "join_audio"
            else if (menu item "Mute audio" of menu 1 of menu bar item "Meeting" of menu bar 1 exists) or (menu item "Mute telephone" of menu 1 of menu bar item "Meeting" of menu bar 1 exists) then
                return "muted"
            else if (menu item "Unmute audio" of menu 1 of menu bar item "Meeting" of menu bar 1 exists) or (menu item "Unmute telephone" of menu 1 of menu bar item "Meeting" of menu bar 1 exists) then
                return "unmuted"
            else
                return "no_meeting"
            end if
        end tell
    else
        return "not_running"
    end if
end tell
'''
# Click Zoom's own Meeting-menu items via System Events on the zoom.us
# process — the same targeting STATUS_SCRIPT already uses to read them —
# rather than a keystroke that would land on whatever app is frontmost.
# Audio vs. telephone participants expose different item labels for the
# same action, so mute/unmute try both, same as STATUS_SCRIPT's exists checks.
JOIN_AUDIO_SCRIPT = '''
tell application "System Events"
    tell application process "zoom.us"
        click menu item "Join Audio" of menu 1 of menu bar item "Meeting" of menu bar 1
    end tell
end tell
'''
MUTE_SCRIPT = '''
tell application "System Events"
    tell application process "zoom.us"
        if menu item "Mute audio" of menu 1 of menu bar item "Meeting" of menu bar 1 exists then
            click menu item "Mute audio" of menu 1 of menu bar item "Meeting" of menu bar 1
        else if menu item "Mute telephone" of menu 1 of menu bar item "Meeting" of menu bar 1 exists then
            click menu item "Mute telephone" of menu 1 of menu bar item "Meeting" of menu bar 1
        end if
    end tell
end tell
'''
UNMUTE_SCRIPT = '''
tell application "System Events"
    tell application process "zoom.us"
        if menu item "Unmute audio" of menu 1 of menu bar item "Meeting" of menu bar 1 exists then
            click menu item "Unmute audio" of menu 1 of menu bar item "Meeting" of menu bar 1
        else if menu item "Unmute telephone" of menu 1 of menu bar item "Meeting" of menu bar 1 exists then
            click menu item "Unmute telephone" of menu 1 of menu bar item "Meeting" of menu bar 1
        end if
    end tell
end tell
'''
LAUNCH_SCRIPT = 'tell application "zoom.us" to activate'

KNOWN_STATES = {"not_running", "no_meeting", "join_audio", "muted", "unmuted"}


def run(cmd, timeout):
    """Runs `cmd`, degrading to a failure tuple on any error/timeout rather
    than raising — a slow/denied System Events call must degrade, not
    crash the menu."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception:
        return 1, "", ""


INSTALLED = any(
    os.path.isdir(p)
    for p in ("/Applications/zoom.us.app", os.path.expanduser("~/Applications/zoom.us.app"))
)

if not INSTALLED:
    state = "not_installed"
else:
    rc, out, err = run(["osascript", "-e", STATUS_SCRIPT], timeout=3)
    if rc != 0:
        # -1743: "not authorized to send Apple events" — the Automation
        # permission was denied or not yet granted.
        state = "not_authorized" if "-1743" in err else "error"
    else:
        state = out.strip()
        if state not in KNOWN_STATES:
            state = "error"

menu = JSONMenu()
d = menu.dropdown

if state == "not_installed":
    menu.title("Zoom", sfimage="questionmark.circle", color=DIM)
    d.item("Zoom is not installed", disabled=True)
    d.item("Download Zoom", href="https://zoom.us/download", sfimage="arrow.down.circle")

elif state == "not_running":
    menu.title("Zoom", sfimage="power", color=DIM)
    d.item("Zoom is not running", disabled=True)
    d.item("Launch Zoom", sfimage="power", shell="/usr/bin/osascript",
            params=["-e", LAUNCH_SCRIPT], refresh=True)

elif state == "no_meeting":
    menu.title("Zoom", sfimage="video", color=DIM)
    d.item("Not in a meeting", disabled=True)

elif state == "join_audio":
    menu.title("Join audio", sfimage="mic", color=AMBER)
    d.item("Waiting to join meeting audio", disabled=True)
    d.item("Join Audio", sfimage="mic", shell="/usr/bin/osascript",
            params=["-e", JOIN_AUDIO_SCRIPT], refresh=True)

elif state == "muted":
    menu.title("Muted", sfimage="mic.slash.fill", color=RED)
    d.item("Unmute", sfimage="mic.fill", shell="/usr/bin/osascript",
            params=["-e", UNMUTE_SCRIPT], refresh=True)

elif state == "unmuted":
    menu.title("Live", sfimage="mic.fill", color=GREEN)
    d.item("Mute", sfimage="mic.slash.fill", shell="/usr/bin/osascript",
            params=["-e", MUTE_SCRIPT], refresh=True)

elif state == "not_authorized":
    menu.title("Zoom", sfimage="exclamationmark.triangle.fill", color=AMBER)
    d.item("Automation permission needed for System Events", disabled=True)
    d.item("Open Automation Settings",
            href="x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
            sfimage="gearshape")
    d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

else:  # "error" — osascript failed for a reason other than permissions, or timed out
    menu.title("Zoom", sfimage="exclamationmark.triangle", color=DIM)
    d.item("Could not read Zoom status", disabled=True)
    d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

menu.print()
