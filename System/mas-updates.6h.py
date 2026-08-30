#!/usr/bin/env python3
#
# mas-updates.6h.py - outdated Mac App Store apps, with per-app and
# upgrade-all actions.
#
# Ports System/mas.1d.sh from matryer/xbar-plugins. Security audit verdict:
# CLEAN (see the audit report that shipped with this port) - the plugin
# itself opens no sockets and touches no credentials; it shells out to
# `mas` (https://github.com/mas-cli/mas), a user-installed, open-source CLI
# that wraps Apple's own App Store frameworks. `mas outdated`/`mas upgrade`
# are exactly this plugin's stated purpose.
#
# Fixes vs. upstream:
#  - `shutil.which("mas")` replaces a hardcoded /usr/local/bin/mas, which
#    misses the default Apple Silicon Homebrew prefix (/opt/homebrew/bin)
#    and would wrongly report "not installed" on most current Macs.
#  - Per-app row runs `mas upgrade <id>`, not upstream's `mas install <id>`
#    - `upgrade` is mas's documented verb for updating an app you already
#    own; `install` is for a fresh purchase. Now matches "Upgrade all",
#    which upstream already spelled correctly as `mas upgrade`.
#  - No `mas` on PATH -> one row with the Homebrew install command,
#    click-to-copy only (never auto-executed), plus a link to the project.
#  - Zero outdated apps -> an explicit "Up to date" row instead of
#    upstream's silent empty output (which left the menu bar showing
#    nothing at all).
#  - JSON output instead of the text protocol: an app name that happens to
#    contain "|" no longer truncates its row.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>Mac App Store Updates</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Outdated Mac App Store apps, with per-app and upgrade-all actions via mas-cli.</vee.desc>
# <vee.dependencies>python3,mas</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,network,clipboard</vee.capabilities>
# <vee.exec>mas,python3,pbcopy</vee.exec>
# <vee.network>apple.com (indirect - mas talks to the App Store, this script never opens a socket itself)</vee.network>

import json
import shutil
import subprocess
import sys


class JSONSection:
    """A dropdown section - see https://vee.navbytes.io/guide/json-output/.
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


# `mas outdated` is a real network round trip against Apple's servers, not a
# local syscall, so it gets more slack than the usual "under 3s" plugin
# budget - mirrors github.5m.py's 8-10s CLI timeouts for the same reason.
# Still well under Vee's 30s default execution cutoff, and this plugin only
# runs every 6h.
MAS_OUTDATED_TIMEOUT_S = 15


def emit(menu):
    menu.print()
    sys.exit(0)


def error_row(message):
    menu = JSONMenu()
    menu.title("!", sfimage="exclamationmark.triangle", color="red")
    menu.dropdown.item(message, color="red")
    menu.dropdown.item("Refresh", refresh=True, sfimage="arrow.clockwise")
    emit(menu)


def copy_to_clipboard_opts(value):
    """Opts for a row whose click copies `value` to the clipboard - the
    value travels as a subprocess argv element, never interpolated into a
    shell string, so it is data and can't be accidentally executed. Same
    pattern as Network/network.30s.py's copy_row."""
    return {
        "shell": "/usr/bin/python3",
        "params": [
            "-c",
            "import subprocess,sys; subprocess.run(['/usr/bin/pbcopy'], input=sys.argv[1].encode())",
            value,
        ],
        "terminal": False,
    }


def not_installed():
    menu = JSONMenu()
    menu.title("?", sfimage="questionmark.circle", color="gray")
    d = menu.dropdown
    d.item("mas-cli not found on PATH", color="gray", disabled=True)
    install_cmd = "brew install mas"
    d.item(
        install_cmd,
        sfimage="doc.on.doc",
        tooltip=f"Click to copy: {install_cmd}",
        **copy_to_clipboard_opts(install_cmd),
    )
    d.item("mas-cli on GitHub", href="https://github.com/mas-cli/mas", sfimage="link")
    emit(menu)


def parse_outdated(text):
    """Each `mas outdated` line is `<app-id> <name> (<installed> -> <latest>)`.
    id is always the first whitespace-delimited token, mirroring upstream's
    `awk '{itemIdentifier = $1; $1 = ""; print $0 ...}'`."""
    apps = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        apps.append((parts[0], parts[1].strip()))
    return apps


def main():
    mas = shutil.which("mas")
    if not mas:
        not_installed()
        return

    try:
        r = subprocess.run([mas, "outdated"], capture_output=True, text=True, timeout=MAS_OUTDATED_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        error_row("Timed out checking for updates")
        return
    except OSError:
        error_row("Couldn't run mas")
        return

    if r.returncode != 0:
        # mas can fail if you're not signed into the App Store, or on a
        # transient network error - say so rather than reporting "no
        # updates" when the check never actually ran. A non-zero exit is
        # never success, even if stdout has partial output.
        first_line = next((ln for ln in (r.stderr or "").splitlines() if ln.strip()), "mas outdated failed")
        error_row(first_line.strip())
        return

    apps = parse_outdated(r.stdout)

    menu = JSONMenu()
    if not apps:
        menu.title("0", sfimage="shippingbox", color="green")
        menu.dropdown.item("Up to date", color="green", sfimage="checkmark.circle")
        menu.dropdown.item("Refresh", refresh=True, sfimage="arrow.clockwise")
        emit(menu)
        return

    menu.title(str(len(apps)), sfimage="shippingbox", color="orange")
    d = menu.dropdown
    d.item(
        "Upgrade all",
        sfimage="arrow.down.circle.fill",
        color="blue",
        shell=mas,
        params=["upgrade"],
        terminal=False,
        refresh=True,
        searchable=False,
        tooltip="Runs `mas upgrade` - upgrades every app listed below. A large batch can be killed by Vee's action execution cap if it runs too long.",
    )
    d.separator()
    for app_id, label in apps:
        d.item(
            label,
            shell=mas,
            params=["upgrade", app_id],
            terminal=False,
            refresh=True,
            searchable=False,
            tooltip=f"Runs `mas upgrade {app_id}`",
        )
    d.separator()
    d.item("Refresh", refresh=True, sfimage="arrow.clockwise")
    emit(menu)


if __name__ == "__main__":
    main()
