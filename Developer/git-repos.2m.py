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
# <xbar.title>Git Repos</xbar.title>
# <xbar.version>1.0</xbar.version>
# <xbar.author>Naveen Kumar</xbar.author>
# <xbar.author.github>navbytes</xbar.author.github>
# <xbar.desc>Uncommitted/unpushed/clean status for every repo under GIT_ROOTS, with a searchable jump panel.</xbar.desc>
# <xbar.dependencies>python3,git</xbar.dependencies>
# <xbar.abouturl>https://github.com/navbytes/vee-plugins</xbar.abouturl>
#
# <xbar.var>string(GIT_ROOTS=~/repos): Colon-separated directories to scan for repos (2 levels deep).</xbar.var>
# <xbar.var>string(GIT_EDITOR_CMD=): Command to open a repo in your editor, e.g. "code" or "subl". Leave blank to hide the menu item.</xbar.var>
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

MAX_REPOS = 40
GIT_ROOTS = os.environ.get("GIT_ROOTS", "~/repos")
GIT_EDITOR_CMD = os.environ.get("GIT_EDITOR_CMD", "").strip()


def emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.exit(0)


def single_row(title_text, row_text, color="gray", sfimage="arrow.triangle.branch"):
    emit(
        {
            "vee": 1,
            "title": [{"text": title_text, "sfimage": sfimage, "color": color}],
            "items": [{"text": row_text, "color": "gray"}],
        }
    )


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
    # three to empty on every invocation neutralizes that regardless of what
    # the repo's own config says.
    gitcmd = [
        "git",
        "-c", "core.fsmonitor=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "include.path=",
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


def repo_item(info, color):
    counts = f"+{info['added']} ~{info['modified']} -{info['deleted']}"
    submenu = [
        {
            "text": "Open in Finder",
            "shell": "/usr/bin/open",
            "params": [info["path"]],
            "sfimage": "finder",
        },
        {
            "text": "Open in Terminal",
            "shell": "/usr/bin/open",
            "params": ["-a", "Terminal", info["path"]],
            "sfimage": "terminal",
        },
    ]
    if GIT_EDITOR_CMD:
        parts = shlex.split(GIT_EDITOR_CMD)
        submenu.append(
            {
                "text": f"Open in {GIT_EDITOR_CMD}",
                "shell": parts[0],
                "params": parts[1:] + [info["path"]],
                "sfimage": "chevron.left.forwardslash.chevron.right",
            }
        )
    if info["github_url"]:
        submenu.append(
            {"text": "Open on GitHub", "href": info["github_url"], "sfimage": "link"}
        )

    return {
        "text": f"{info['name']}  ·  {info['branch']}  ·  {counts}",
        "color": color,
        "tooltip": info["path"],
        "submenu": submenu,
        "alternate": {"text": info["path"], "color": color, "submenu": submenu},
    }


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
            uncommitted.append(repo_item(info, "red"))
        elif info["has_upstream"] and info["ahead"] > 0:
            unpushed.append(repo_item(info, "orange"))
        else:
            clean.append(repo_item(info, "gray"))

    if uncommitted:
        title_text, title_color = f"{len(uncommitted)} dirty", "orange"
    elif unpushed:
        title_text, title_color = f"{len(unpushed)} unpushed", "yellow"
    else:
        title_text, title_color = "Clean", "green"

    items = []
    if uncommitted:
        items.append({"header": True, "text": "Uncommitted changes"})
        items.extend(uncommitted)
    if unpushed:
        if items:
            items.append({"separator": True})
        items.append({"header": True, "text": "Unpushed commits"})
        items.extend(unpushed)
    if clean:
        if items:
            items.append({"separator": True})
        items.append({"header": True, "text": "Clean"})
        items.extend(clean)

    emit(
        {
            "vee": 1,
            "title": [
                {
                    "text": title_text,
                    "sfimage": "arrow.triangle.branch",
                    "color": title_color,
                }
            ],
            "items": items,
        }
    )


if __name__ == "__main__":
    main()
