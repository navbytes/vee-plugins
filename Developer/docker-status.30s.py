#!/usr/bin/env python3
#
# docker-status.30s.py — running and stopped Docker containers.
#
# One `docker ps -a --format '{{json .}}'` call (NDJSON, one object per
# container — no `|`-joined fields to split), grouped into Running and
# Stopped. Each container's row starts or stops it via `shell=`/`params=`.
# Two distinct degradation states: the `docker` CLI missing vs. the CLI
# present but the daemon unreachable.
#
# Ported from xbar's Dev/Docker/docker-status.1m.sh (Manoj Mahalingam,
# https://github.com/matryer/xbar-plugins/blob/main/Dev/Docker/docker-status.1m.sh).
# See the audit/port notes in the PR description for what changed and why.
#
# <vee.title>Docker Status</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Running and stopped Docker containers, with start/stop actions.</vee.desc>
# <vee.dependencies>python3,docker</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# <vee.var>boolean(SHOW_STOPPED=true): Also list stopped/exited containers, not just running ones.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec</vee.capabilities>
# <vee.exec>docker</vee.exec>

import json
import os
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


SHOW_STOPPED = True


def env_bool(name, default):
    v = os.environ.get(name, "").strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return default


SHOW_STOPPED = env_bool("SHOW_STOPPED", SHOW_STOPPED)


def emit(menu):
    menu.print()
    sys.exit(0)


def single_row(title_text, row_text, color="gray"):
    menu = JSONMenu()
    menu.title(title_text, sfimage="shippingbox", color=color)
    menu.dropdown.item(row_text, color="gray")
    emit(menu)


def docker_ps(docker_bin):
    """One `docker ps -a` call as newline-delimited JSON. None means the CLI
    ran but failed — daemon unreachable, permission denied, etc. An empty
    list is a real, successful "no containers" result."""
    try:
        r = subprocess.run(
            [docker_bin, "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    containers = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # ignore a malformed line rather than crash the menu
        if isinstance(obj, dict):
            containers.append(obj)
    return containers


def add_container_item(section, docker_bin, container, running):
    name = container.get("Names", "?")
    image = container.get("Image", "")
    cid = container.get("ID", "")
    status = container.get("Status", "")
    label = f"{name} ({image})" if image else name

    sub = section.submenu(label, color="green" if running else "red", sfimage="circle.fill")
    if status:
        sub.item(status, color="gray", disabled=True)
        sub.separator()
    if running:
        sub.item(
            "Stop container",
            color="red",
            sfimage="stop.circle",
            shell=docker_bin,
            params=["stop", cid],
            tooltip=f"Sends a stop request to {name} ({cid[:12]})",
            searchable=False,
            refresh=True,
        )
    else:
        sub.item(
            "Start container",
            color="green",
            sfimage="play.circle",
            shell=docker_bin,
            params=["start", cid],
            tooltip=f"Starts {name} ({cid[:12]})",
            searchable=False,
            refresh=True,
        )


def main():
    docker_bin = shutil.which("docker")
    if not docker_bin:
        single_row("Docker not installed", "docker CLI not found on PATH", color="red")

    containers = docker_ps(docker_bin)
    if containers is None:
        single_row("Docker not running", "Couldn't reach the Docker daemon", color="red")

    running = [c for c in containers if c.get("State") == "running"]
    stopped = [c for c in containers if c.get("State") != "running"]

    menu = JSONMenu()
    if running:
        menu.title(f"{len(running)} running", sfimage="shippingbox.fill", color="blue")
    else:
        menu.title("Docker", sfimage="shippingbox", color="gray")
    dropdown = menu.dropdown

    if not containers:
        dropdown.item("No containers", sfimage="checkmark.circle", color="green")
        emit(menu)

    any_items = False
    if running:
        dropdown.item("Running", header=True)
        for c in running:
            add_container_item(dropdown, docker_bin, c, running=True)
        any_items = True

    if SHOW_STOPPED and stopped:
        if any_items:
            dropdown.separator()
        dropdown.item("Stopped", header=True)
        for c in stopped:
            add_container_item(dropdown, docker_bin, c, running=False)
        any_items = True

    if not any_items:
        # Everything that exists is stopped and SHOW_STOPPED hid it.
        dropdown.item(f"{len(stopped)} stopped container(s) hidden (SHOW_STOPPED=false)", color="gray")

    emit(menu)


if __name__ == "__main__":
    main()
