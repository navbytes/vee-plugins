#!/usr/bin/env python3
#
# git-repos.2m.py — the state of every git repo you're working in.
#
# Scans each directory in GIT_ROOTS two levels deep (root/*/.git and
# root/*/.git, i.e. repos directly under a root or one folder deeper — a
# repo living at ~/repos/work/some-repo is found, nothing past that), skips
# anything without a .git, and buckets what's left into "Uncommitted
# changes", "Unpushed commits", and "Clean". Never touches the network —
# `git status`/`rev-list`/`remote get-url` are all local-only reads.
#
# This is the searchable-filter-panel showcase: <vee.filter>true</vee.filter>
# plus a global hotkey turn the dropdown into a type-to-jump repo picker.
#
# <vee.title>Git Repos</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Uncommitted/unpushed/clean status for every repo under GIT_ROOTS, with a searchable jump panel.</vee.desc>
# <vee.dependencies>python3,git</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/git-repos.png</vee.image>
#
# <vee.var>string(GIT_ROOTS=~/repos): Colon-separated directories to scan for repos (2 levels deep).</vee.var>
# <vee.var>string(GIT_EDITOR_CMD=): Command to open a repo in your editor, e.g. "code" or "subl". Leave blank to hide the menu item.</vee.var>
#
# <vee.filter>true</vee.filter>
# <vee.shortcut>cmd+shift+g</vee.shortcut>
#
# Trust declarations (advisory, never enforced):
# <vee.exec>git,open,$GIT_EDITOR_CMD (user-configured; blank by default, see xbar.var above)</vee.exec>
# <vee.filesystem.read>~/repos (configurable via GIT_ROOTS)</vee.filesystem.read>

import json
import os
import re
import shlex
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


MAX_REPOS = 40
GIT_ROOTS = os.environ.get("GIT_ROOTS", "~/repos")
GIT_EDITOR_CMD = os.environ.get("GIT_EDITOR_CMD", "").strip()


def emit(menu):
    menu.print()
    sys.exit(0)


def single_row(title_text, row_text, color="gray", sfimage="arrow.triangle.branch"):
    menu = JSONMenu()
    menu.title(title_text, sfimage=sfimage, color=color)
    menu.dropdown.item(row_text, color="gray")
    emit(menu)


def run(cmd, cwd):
    """Run a git command, --no-optional-locks already baked into cmd. Never
    raises — a missing upstream or a weird repo state should degrade to an
    empty string, not blow up the whole menu."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=3
        )
        return r.stdout
    except Exception:
        return ""


def find_repos(roots):
    """Non-recursive beyond depth 2: root/*/.git and root/*/*/.git only."""
    repos = []
    for raw in roots.split(":"):
        root = os.path.expanduser(raw.strip())
        if not root or not os.path.isdir(root):
            continue
        try:
            depth1 = sorted(os.listdir(root))
        except OSError:
            continue
        for name1 in depth1:
            p1 = os.path.join(root, name1)
            if not os.path.isdir(p1):
                continue
            if os.path.exists(os.path.join(p1, ".git")):
                repos.append(p1)
                if len(repos) >= MAX_REPOS:
                    return repos
                continue  # a repo itself isn't scanned for nested repos
            try:
                depth2 = sorted(os.listdir(p1))
            except OSError:
                continue
            for name2 in depth2:
                p2 = os.path.join(p1, name2)
                if os.path.isdir(p2) and os.path.exists(os.path.join(p2, ".git")):
                    repos.append(p2)
                    if len(repos) >= MAX_REPOS:
                        return repos
    return repos


def to_github_url(remote):
    if not remote or "github.com" not in remote:
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", remote.strip())
    return f"https://github.com/{m.group(1)}" if m else None


def repo_info(path):
    # GIT_ROOTS gets scanned unattended, and any of these repos could be a
    # freshly-extracted zip/tarball whose .git/config was never reviewed. A
    # config that sets core.fsmonitor/core.hooksPath/include.path to an
    # arbitrary script would otherwise execute the moment `git status` (or
    # any other git call below) reads it — no click required. Forcing all
    # these to empty on every invocation neutralizes that regardless of what
    # the repo's own config says. (`-c include.path=` was tried here too and
    # is a trap: git rejects an empty include.path with "relative config
    # includes must come from files" and exits 128, so EVERY git call failed
    # and every repo silently reported clean. It bought nothing anyway — a
    # command-line -c cannot disable includes the repo's own config declares.)
    gitcmd = [
        "git",
        "-c", "core.fsmonitor=",
        "-c", "core.hooksPath=/dev/null",
        "--no-optional-locks", "-C", path,
    ]

    status_out = run(gitcmd + ["status", "--porcelain=v1"], path)
    added = modified = deleted = 0
    for line in status_out.splitlines():
        code = line[:2]
        if code == "??":
            added += 1
            continue
        x, y = (code + "  ")[0], (code + "  ")[1]
        if "A" in (x, y):
            added += 1
        elif "D" in (x, y):
            deleted += 1
        else:
            modified += 1
    dirty = bool(added or modified or deleted)

    branch = run(gitcmd + ["rev-parse", "--abbrev-ref", "HEAD"], path).strip() or "HEAD"

    rl = run(gitcmd + ["rev-list", "--left-right", "--count", "@{u}...HEAD"], path).strip()
    has_upstream = bool(rl)
    ahead = 0
    if has_upstream:
        parts = rl.split()
        if len(parts) == 2:
            ahead = int(parts[1])

    remote = run(gitcmd + ["remote", "get-url", "origin"], path).strip()

    return {
        "name": os.path.basename(path.rstrip("/")),
        "path": path,
        "branch": branch,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "dirty": dirty,
        "ahead": ahead,
        "has_upstream": has_upstream,
        "github_url": to_github_url(remote),
    }


def add_repo_actions(section, info):
    """Populates a repo's action rows (shared by its submenu and its
    alternate's submenu)."""
    section.item(
        "Open in Finder", shell="/usr/bin/open", params=[info["path"]], sfimage="finder"
    )
    section.item(
        "Open in Terminal",
        shell="/usr/bin/open",
        params=["-a", "Terminal", info["path"]],
        sfimage="terminal",
    )
    if GIT_EDITOR_CMD:
        parts = shlex.split(GIT_EDITOR_CMD)
        section.item(
            f"Open in {GIT_EDITOR_CMD}",
            shell=parts[0],
            params=parts[1:] + [info["path"]],
            sfimage="chevron.left.forwardslash.chevron.right",
        )
    if info["github_url"]:
        section.item("Open on GitHub", href=info["github_url"], sfimage="link")


def repo_alternate(info, color):
    """The path-form row shown when a modifier key is held, with the same
    submenu actions as the main row."""
    rows: list = []
    add_repo_actions(JSONSection(rows), info)
    return {"text": info["path"], "color": color, "submenu": rows}


def add_repo_item(section, info, color):
    counts = f"+{info['added']} ~{info['modified']} -{info['deleted']}"
    sub = section.submenu(
        f"{info['name']}  ·  {info['branch']}  ·  {counts}",
        color=color,
        tooltip=info["path"],
        alternate=repo_alternate(info, color),
    )
    add_repo_actions(sub, info)


def main():
    if not shutil.which("git"):
        single_row("git missing", "git not found on PATH", color="red")

    repos = find_repos(GIT_ROOTS)
    if not repos:
        single_row(
            "No repos",
            f"No repos found under GIT_ROOTS ({GIT_ROOTS})",
            color="gray",
        )

    uncommitted, unpushed, clean = [], [], []
    for path in repos:
        info = repo_info(path)
        if info["dirty"]:
            uncommitted.append(info)
        elif info["has_upstream"] and info["ahead"] > 0:
            unpushed.append(info)
        else:
            clean.append(info)

    if uncommitted:
        title_text, title_color = f"{len(uncommitted)} dirty", "orange"
    elif unpushed:
        title_text, title_color = f"{len(unpushed)} unpushed", "yellow"
    else:
        title_text, title_color = "Clean", "green"

    menu = JSONMenu()
    menu.title(title_text, sfimage="arrow.triangle.branch", color=title_color)
    dropdown = menu.dropdown

    any_items = False
    if uncommitted:
        dropdown.item("Uncommitted changes", header=True)
        for info in uncommitted:
            add_repo_item(dropdown, info, "red")
        any_items = True
    if unpushed:
        if any_items:
            dropdown.separator()
        dropdown.item("Unpushed commits", header=True)
        for info in unpushed:
            add_repo_item(dropdown, info, "orange")
        any_items = True
    if clean:
        if any_items:
            dropdown.separator()
        dropdown.item("Clean", header=True)
        for info in clean:
            add_repo_item(dropdown, info, "gray")
        any_items = True

    emit(menu)


if __name__ == "__main__":
    main()
