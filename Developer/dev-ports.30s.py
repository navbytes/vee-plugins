#!/usr/bin/env python3
#
# dev-ports.30s.py — what's listening on your development ports.
#
# One `lsof -nP -iTCP -sTCP:LISTEN -w` call, parsed and split into the ports
# you told it to care about (PORT_RANGES) and everything else (only shown if
# SHOW_ALL). Each listener's submenu can open it in the browser, copy its
# URL, or kill the process — the kill row is deliberately unsearchable so a
# typed query + Return can never land on it.
#
# <vee.title>Dev Ports</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Processes listening on your dev ports, with open/copy/kill actions.</vee.desc>
# <vee.dependencies>python3,lsof</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/dev-ports.png</vee.image>
#
# <vee.var>string(PORT_RANGES=3000-3010,4000,5173,8000-8100,8080,9000): Comma-separated ports/ranges to watch, e.g. "3000-3010,8080".</vee.var>
# <vee.var>boolean(SHOW_ALL=false): Also list every other listening port, not just PORT_RANGES.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>lsof,pbcopy,kill,sh</vee.exec>

import json
import re
import shutil
import subprocess
import sys


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


PORT_RANGES = "3000-3010,4000,5173,8000-8100,8080,9000"
SHOW_ALL = False


def env_str(name, default):
    import os

    return os.environ.get(name, default)


def env_bool(name, default):
    import os

    v = os.environ.get(name, "").strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return default


PORT_RANGES = env_str("PORT_RANGES", PORT_RANGES)
SHOW_ALL = env_bool("SHOW_ALL", SHOW_ALL)


def emit(menu):
    menu.print()
    sys.exit(0)


def single_row(title_text, row_text, color="gray"):
    menu = JSONMenu()
    menu.title(title_text, sfimage="network", color=color)
    menu.dropdown.item(row_text, color="gray")
    emit(menu)


def parse_ranges(spec):
    ranges = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                ranges.append((int(a), int(b)))
            else:
                n = int(part)
                ranges.append((n, n))
        except ValueError:
            continue  # ignore a malformed entry rather than crash the menu
    return ranges


def in_ranges(port, ranges):
    return any(a <= port <= b for a, b in ranges)


def unescape(s):
    # lsof escapes odd bytes in COMMAND (with +c0) as \xHH, e.g. spaces.
    return re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)


NAME_RE = re.compile(r"^.*:(\d+)$")


def listeners():
    """One lsof call, deduped by (pid, port) since dual-stack processes list
    an IPv4 and an IPv6 line for the same port."""
    try:
        r = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-w", "+c0"],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return None
    seen = {}
    for line in r.stdout.splitlines()[1:]:  # skip header
        cols = line.split()
        if len(cols) < 9 or cols[-1] != "(LISTEN)":
            continue
        command, pid, user, name = cols[0], cols[1], cols[2], cols[-2]
        m = NAME_RE.match(name)
        if not m:
            continue
        port = int(m.group(1))
        key = (pid, port)
        if key not in seen:
            seen[key] = {"port": port, "command": unescape(command), "pid": pid, "user": user}
    return sorted(seen.values(), key=lambda x: x["port"])


def add_listener_item(section, entry, matched):
    port, command, pid = entry["port"], entry["command"], entry["pid"]
    url = f"http://localhost:{port}"
    sub = section.submenu(
        f":{port} — {command} (pid {pid})",
        color="blue" if matched else "gray",
        sfimage="circle.fill",
    )
    sub.item(f"Open {url}", href=url, sfimage="safari")
    sub.item(
        "Copy URL",
        shell="/bin/sh",
        params=["-c", f"printf '%s' {json.dumps(url)} | /usr/bin/pbcopy"],
        sfimage="doc.on.doc",
        tooltip=f"Copies {url} to the clipboard",
    )
    sub.separator()
    sub.item(
        f"Kill process (pid {pid})",
        color="red",
        sfimage="xmark.octagon",
        shell="/bin/kill",
        params=[pid],
        tooltip=f"Sends SIGTERM to {command} (pid {pid})",
        searchable=False,
    )


def main():
    if not shutil.which("lsof"):
        single_row("lsof missing", "lsof not found on PATH", color="red")

    all_listeners = listeners()
    if all_listeners is None:
        single_row("lsof failed", "Couldn't run lsof (timed out or errored)", color="red")

    ranges = parse_ranges(PORT_RANGES)
    configured = [e for e in all_listeners if in_ranges(e["port"], ranges)]
    other = [e for e in all_listeners if not in_ranges(e["port"], ranges)]

    title_color = "blue" if configured else "gray"
    title_text = f"{len(configured)} ports"

    menu = JSONMenu()
    menu.title(title_text, sfimage="network", color=title_color)
    dropdown = menu.dropdown

    any_items = False
    if configured:
        dropdown.item("Configured ports", header=True)
        for e in configured:
            add_listener_item(dropdown, e, True)
        any_items = True
    if SHOW_ALL and other:
        if any_items:
            dropdown.separator()
        dropdown.item("Other listeners", header=True)
        for e in other:
            add_listener_item(dropdown, e, False)
        any_items = True

    if not any_items:
        dropdown.item(
            "Nothing listening on your dev ports",
            sfimage="checkmark.circle",
            color="green",
        )

    emit(menu)


if __name__ == "__main__":
    main()
