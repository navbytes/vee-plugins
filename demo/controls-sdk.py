#!/usr/bin/env python3
#
# controls-sdk.py — every menu-row control Vee's plugin format supports, in
# one file, built with the vendored SDK. `controls-raw.py` builds the exact
# same menu by hand, with no SDK, so the two are meant to be read side by
# side — see README.md in this folder.
#
# This is a reference, not a utility: every row is a static example, nothing
# reads real system state. The two interactive rows (toggle/slider) and the
# "runs a command" row shell out to /bin/echo, whose output goes nowhere —
# a harmless stand-in for a real command. href/webview only ever open this
# project's own GitHub page.
#
# <vee.title>Controls Demo (SDK)</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Every control the Vee plugin format supports, built with the SDK.</vee.desc>
# <vee.dependencies>python3</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.filter>true</vee.filter>
#
# Trust declarations (advisory, never enforced): the three rows described
# above are the only ones that run anything, and they only run on click.
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>echo</vee.exec>

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from vee import Menu

# A 1x1 transparent PNG — stands in for real artwork so `image=`/
# `template_image=` have something to render.
PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGNgAAACAAEA3Y0qPQAA"
    "AABJRU5ErkJggg=="
)
DOCS_URL = "https://github.com/navbytes/vee-plugins"

menu = Menu()
# Multiple title lines: Vee cycles/stacks them in the menu bar.
menu.title("Controls Demo", sfimage="slider.horizontal.3", color="blue")
menu.title("SDK", color="gray", size=11)

d = menu.dropdown

# ---------------------------------------------------------------------------
d.item("Text & rendering", header=True)
d.item("Colored text", color="purple")
d.item("Monospaced, 14pt", font="Menlo", size=14)
d.item("Truncated to 12 characters from a much longer string", length=12)
d.item("   trimmed of its own leading/trailing space   ", trim=True)
d.item("\033[32mANSI is interpreted by default\033[0m")
d.item("\033[32mansi=false shows the raw escape codes\033[0m", ansi=False)
d.item("Emoji shortcode :smile: rendered via emojize=true", emojize=True)
d.item("**Bold** and _italic_ via md=true", md=True)
# A literal | or \ in display text has to be escaped or it corrupts the
# line — a bare | is read as the text/params delimiter. The SDK does this
# for you; controls-raw.py does it by hand.
d.item("Reserved chars: a|b and a back\\slash, escaped automatically")
d.separator()

# ---------------------------------------------------------------------------
d.item("Click behavior", header=True)
d.item("Open the docs", href=DOCS_URL)
d.item(
    "Run a harmless command",
    shell="/bin/echo",
    params=["hello from the SDK demo"],
    terminal=False,
    tooltip="Runs /bin/echo in the background; output goes nowhere",
)
d.item("Refresh this menu", refresh=True)
d.item("Hold Option to see the alternate below")
d.item("Option: this row replaces the one above it", alternate=True)
d.item("Disabled row — greyed out, not clickable", disabled=True)
d.item("Has a keyboard shortcut while the menu is open", key="cmd+shift+d")
d.item(
    "Runs a named macOS Shortcut (only if one exists with this name)",
    shortcut="Say Hello",
)
d.item("Opens a WebView window", webview=DOCS_URL, webview_w=480, webview_h=360)
d.separator()

# ---------------------------------------------------------------------------
d.item("Placement & visibility", header=True)
d.item(
    "Its accessory is anchored to the leading edge",
    progress=0.4,
    accessory="leading",
    accessory_w=100,
    accessory_h=8,
)
d.item(
    "Only visible in the menu and a detached window",
    visible_on=["menu", "window"],
    tooltip="Absent from the search panel and vee search",
)
d.item(
    "searchable=false — browsable, never search-matched",
    searchable=False,
    tooltip="A typed query plus Return can never land on this row",
)
d.separator()

# ---------------------------------------------------------------------------
d.item("Images", header=True)
d.item("image= (base64 PNG)", image=PIXEL_PNG)
d.item("template_image= (adapts to light/dark)", template_image=PIXEL_PNG)
d.separator()

# ---------------------------------------------------------------------------
d.item("SF Symbols", header=True)
d.item("sfimage=", sfimage="cpu")
d.item("sfimage= + sf_color=", sfimage="battery.100", sf_color="green")
d.item(
    "sfimage= + multicolor sf_color list + sf_size=",
    sfimage="person.crop.circle.badge.checkmark",
    sf_color=["blue", "green"],
    sf_size=18,
)
d.item(
    "sfimage= + sf_config=",
    sfimage="bolt.fill",
    sf_config='{"scale":"large","weight":"bold"}',
)
d.item("Inline glyph: status :checkmark.circle: via symbolize=true", symbolize=True)
d.separator()

# ---------------------------------------------------------------------------
d.item("Annotations", header=True)
d.item("Hover for a tooltip", tooltip="This is the tooltip text")
d.item("Checked row", checked=True)
d.item("Inbox", badge="12")
d.separator()

# ---------------------------------------------------------------------------
d.item("Charts & gauges", header=True)
d.item(
    "Load history",
    sparkline=[1, 2, 3, 5, 8, 13, 8, 5, 3, 2],
    sparkline_color="teal",
    accessory_w=140,
    accessory_h=20,
)
d.item(
    "Disk usage (fraction)",
    color="green",
    progress=0.72,
    progress_track_color="#333333",
    accessory_w=120,
    accessory_h=8,
)
d.item("Storage (value,max)", color="orange", progress={"value": 23.65, "max": 100})
d.item(
    "By category (pie)",
    chart={"kind": "pie", "values": [45, 30, 25], "labels": ["Documents", "Photos", "Apps"]},
)
d.item(
    "By volume (donut)",
    chart={
        "kind": "donut",
        "values": [512, 256, 128],
        "labels": ["Macintosh HD", "Backup", "Scratch"],
        "colors": ["blue", "teal", "orange"],
    },
)
d.item(
    "Budget (stacked bar, full width)",
    chart={
        "kind": "stackedbar",
        "values": [60, 25, 15],
        "labels": ["Used", "Cache", "Free"],
        "w": "full",
        "h": 14,
    },
)
d.separator()

# ---------------------------------------------------------------------------
d.item("Interactive controls", header=True)
d.item(
    "Notifications",
    toggle=True,
    shell="/bin/echo",
    params=["toggle ->"],
    refresh=True,
    searchable=False,
)
d.item(
    "Volume",
    slider={"min": 0, "max": 100, "value": 40},
    shell="/bin/echo",
    params=["slider ->"],
    accessory_w=120,
    refresh=True,
    searchable=False,
)
d.separator()

# ---------------------------------------------------------------------------
d.item("Nesting", header=True)
sub = d.submenu("Recent", sfimage="clock")
week = sub.submenu("This week")
week.item("#4210 passed", color="green")
week.item("#4209 failed", color="red")
sub.item("Last week", color="gray")
d.separator()
d.item("Refresh", refresh=True, sfimage="arrow.clockwise")

menu.print()
