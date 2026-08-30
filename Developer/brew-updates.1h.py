#!/usr/bin/env python3
#
# brew-updates.1h.py — outdated Homebrew formulas and casks, with one-click
# upgrades.
#
# Ported from xbar's Ruby "Brew Updates" plugin (Jim Myhrberg / jimeh,
# github.com/matryer/xbar-plugins/blob/main/Dev/Homebrew/brew-updates.1h.rb).
# Rewritten from scratch as dependency-free Python 3 against `brew outdated
# --json=v2` — the original needed a Ruby interpreter, which this store's bar
# (nothing beyond what macOS ships) doesn't allow. Same idea: one JSON call,
# split into formulas / casks / pinned, upgrade or uninstall from a submenu.
#
# This script makes no network calls of its own — `brew update` and `brew
# outdated` may reach Homebrew's own infrastructure, but that's brew's doing,
# not this script's. The background check (run every interval) sets
# HOMEBREW_NO_AUTO_UPDATE=1 so a stale tap can never turn a routine menu
# refresh into a multi-minute `brew update`; use the explicit "Update
# Homebrew & Refresh" row when you want a real update.
#
# The upstream plugin also had an in-menu "Settings" submenu with toggle
# rows that RPC'd back into the script to persist config — Vee has no
# equivalent RPC mechanism, so those became typed <vee.var> preferences
# instead (Vee builds the form). Same for its "Upgrade All: Exclude" toggle
# per package: it's now the UPGRADE_ALL_EXCLUDE preference. The upstream
# "Post-Run: Doctor" toggle is dropped — `brew doctor`'s output isn't shown
# anywhere in a menu-bar model, so silently running it after every upgrade
# bought nothing.
#
# <vee.title>Brew Updates</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Outdated Homebrew formulas and casks, with one-click upgrades.</vee.desc>
# <vee.dependencies>python3</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# <vee.var>string(BREW_PATH=): Path to the "brew" executable. Auto-detected (PATH, then /opt/homebrew, then /usr/local) if left blank.</vee.var>
# <vee.var>boolean(GREEDY_LATEST=false): Also flag casks versioned "latest" as outdated (brew outdated --greedy-latest).</vee.var>
# <vee.var>boolean(GREEDY_AUTO_UPDATES=false): Also flag casks that auto-update themselves (brew outdated --greedy-auto-updates).</vee.var>
# <vee.var>boolean(POST_RUN_CLEANUP=false): Run "brew cleanup" after any upgrade or uninstall.</vee.var>
# <vee.var>string(UPGRADE_ALL_EXCLUDE=): Comma-separated formula/cask names to leave out of "Upgrade All", e.g. "node,docker".</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>exec,network,filesystem</vee.capabilities>
# <vee.exec>brew,sh</vee.exec>
# <vee.network>formulae.brew.sh, ghcr.io, cask vendor hosts (indirect - brew talks to these, this script never opens a socket itself)</vee.network>
# <vee.filesystem.write>/opt/homebrew, /usr/local, /Applications (indirect - via brew upgrade, this script never writes files itself)</vee.filesystem.write>

import json
import os
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


OUTDATED_TIMEOUT = 15  # brew reads local tap/Cellar state; no network with NO_AUTO_UPDATE=1


def env_str(name, default):
    return os.environ.get(name, default)


def env_bool(name, default):
    v = os.environ.get(name, "").strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return default


BREW_PATH = env_str("BREW_PATH", "")
GREEDY_LATEST = env_bool("GREEDY_LATEST", False)
GREEDY_AUTO_UPDATES = env_bool("GREEDY_AUTO_UPDATES", False)
POST_RUN_CLEANUP = env_bool("POST_RUN_CLEANUP", False)
UPGRADE_ALL_EXCLUDE = {
    n.strip() for n in env_str("UPGRADE_ALL_EXCLUDE", "").split(",") if n.strip()
}


def emit(menu):
    menu.print()
    sys.exit(0)


def single_row(title_text, row_text, color="gray", href=None):
    menu = JSONMenu()
    menu.title(title_text, sfimage="shippingbox", color=color)
    menu.dropdown.item(row_text, color=color, href=href)
    emit(menu)


def find_brew():
    override = BREW_PATH.strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which("brew")
    if found:
        return found
    for path in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def chain_shell(brew_path, *commands):
    """Build (shell, params) for one or more `brew <args>` invocations.
    A single command runs directly (no shell); more than one is chained
    with `&&` through /bin/sh -c, each token quoted with shlex."""
    full = [[brew_path] + list(c) for c in commands]
    if len(full) == 1:
        return full[0][0], full[0][1:]
    script = " && ".join(shlex.join(c) for c in full)
    return "/bin/sh", ["-c", script]


def package_shell(brew_path, *args, cleanup=False):
    cmds = [list(args)]
    if cleanup:
        cmds.append(["cleanup"])
    return chain_shell(brew_path, *cmds)


def upgrade_all_shell(brew_path, formula_names, cask_names, cleanup):
    cmds = []
    if formula_names:
        cmds.append(["upgrade", "--formula", "--"] + formula_names)
    if cask_names:
        cmds.append(["upgrade", "--cask", "--"] + cask_names)
    if cleanup:
        cmds.append(["cleanup"])
    return chain_shell(brew_path, *cmds)


def fetch_outdated(brew_path):
    """Run `brew outdated --json=v2`, timeout-guarded. Returns the parsed
    dict, or a string with an error explanation on failure."""
    args = [brew_path, "outdated", "--json=v2"]
    if GREEDY_LATEST:
        args.append("--greedy-latest")
    if GREEDY_AUTO_UPDATES:
        args.append("--greedy-auto-updates")

    env = dict(os.environ)
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"

    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=OUTDATED_TIMEOUT, env=env
        )
    except subprocess.TimeoutExpired:
        return f"brew outdated timed out after {OUTDATED_TIMEOUT}s"
    except Exception as e:
        return f"Couldn't run brew outdated: {e}"

    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        detail = (r.stderr or r.stdout or "no output").strip().splitlines()
        return "brew outdated returned invalid JSON" + (f": {detail[0]}" if detail else "")


def add_package_item(section, pkg, kind, excluded, brew_path):
    name = pkg["name"]
    installed = ", ".join(pkg.get("installed_versions") or []) or "unknown"
    latest = pkg.get("current_version") or "unknown"
    label = f"{name} (excluded)" if excluded else name

    sub = section.submenu(
        label, sfimage="shippingbox", color="gray" if excluded else "blue"
    )

    shell, params = package_shell(
        brew_path, "upgrade", f"--{kind}", "--", name, cleanup=POST_RUN_CLEANUP
    )
    sub.item(
        "Upgrade",
        sfimage="arrow.up.circle",
        color="blue",
        terminal=True,
        refresh=True,
        shell=shell,
        params=params,
        tooltip=f"Runs in Terminal so you can watch it{' (then brew cleanup)' if POST_RUN_CLEANUP else ''}",
        searchable=False,
        alternate={
            "text": f"Upgrade ({installed} → {latest})",
            "shell": shell,
            "params": params,
            "terminal": True,
            "refresh": True,
            "searchable": False,
        },
    )
    sub.separator()
    sub.item(f"Installed: {installed}")
    sub.item(f"Latest: {latest}")
    sub.separator()

    if kind == "formula":
        pin_shell, pin_params = package_shell(brew_path, "pin", "--", name)
        sub.item(
            "Pin",
            sfimage="pin",
            terminal=False,
            refresh=True,
            shell=pin_shell,
            params=pin_params,
            tooltip=f"Freezes {name} at {pkg.get('current_version') or installed}",
        )

    add_uninstall_confirm(sub, kind, name, brew_path)


def add_pinned_item(section, pkg, brew_path):
    name = pkg["name"]
    installed = ", ".join(pkg.get("installed_versions") or []) or "unknown"
    pinned_version = pkg.get("pinned_version") or "unknown"
    latest = pkg.get("current_version") or "unknown"

    sub = section.submenu(name, sfimage="pin.fill", color="gray")
    sub.item(f"Update available: {pinned_version} → {latest}", color="gray")
    sub.separator()
    sub.item(f"Installed: {installed}")
    sub.item(f"Pinned at: {pinned_version}")
    sub.separator()

    unpin_shell, unpin_params = package_shell(brew_path, "unpin", "--", name)
    sub.item(
        "Unpin",
        sfimage="pin.slash",
        terminal=False,
        refresh=True,
        shell=unpin_shell,
        params=unpin_params,
    )
    add_uninstall_confirm(sub, "formula", name, brew_path)


def add_uninstall_confirm(section, kind, name, brew_path):
    confirm = section.submenu("Uninstall", sfimage="trash", color="red")
    confirm.item("Are you sure?", header=True, color="red")
    shell, params = package_shell(
        brew_path, "uninstall", f"--{kind}", "--", name, cleanup=POST_RUN_CLEANUP
    )
    confirm.item(
        f"Yes, uninstall {name}",
        color="red",
        sfimage="xmark.octagon",
        terminal=True,
        refresh=True,
        shell=shell,
        params=params,
        searchable=False,
    )


def main():
    brew_path = find_brew()
    if brew_path is None:
        single_row(
            "brew missing",
            "Homebrew not found — visit brew.sh to install",
            color="red",
            href="https://brew.sh",
        )
        return

    outdated = fetch_outdated(brew_path)
    if isinstance(outdated, str):
        single_row("brew error", outdated, color="red")
        return
    if not isinstance(outdated, dict):
        single_row("brew error", "brew outdated returned unexpected JSON shape", color="red")
        return

    # A malformed entry (not a dict, or missing "name") can never reach a
    # menu row -- skip it here so nothing downstream has to re-check.
    all_formulas = [f for f in (outdated.get("formulae") or []) if isinstance(f, dict) and f.get("name")]
    all_casks = [c for c in (outdated.get("casks") or []) if isinstance(c, dict) and c.get("name")]
    formulas = [f for f in all_formulas if not f.get("pinned")]
    pinned = [f for f in all_formulas if f.get("pinned")]
    casks = all_casks

    total = len(formulas) + len(casks)
    title_color = "blue" if total else "gray"
    title_text = f"{total} outdated" if total else "Up to date"

    menu = JSONMenu()
    menu.title(title_text, sfimage="shippingbox", color=title_color)
    dropdown = menu.dropdown

    dropdown.item("Refresh", sfimage="arrow.clockwise", refresh=True)
    dropdown.item(
        "Update Homebrew & Refresh",
        sfimage="arrow.triangle.2.circlepath",
        shell=brew_path,
        params=["update"],
        terminal=True,
        refresh=True,
        tooltip="Runs brew update in Terminal, then re-checks for outdated packages.",
    )

    configured_formulas = [f for f in formulas if f["name"] not in UPGRADE_ALL_EXCLUDE]
    configured_casks = [c for c in casks if c["name"] not in UPGRADE_ALL_EXCLUDE]
    excluded = [
        p for p in formulas + casks if p["name"] in UPGRADE_ALL_EXCLUDE
    ]

    if configured_formulas or configured_casks:
        dropdown.separator()
        formula_names = [f["name"] for f in configured_formulas]
        cask_names = [c["name"] for c in configured_casks]

        if formula_names and cask_names:
            shell, params = upgrade_all_shell(
                brew_path, formula_names, cask_names, POST_RUN_CLEANUP
            )
            dropdown.item(
                f"Upgrade All ({len(formula_names) + len(cask_names)})",
                sfimage="arrow.up.circle.fill",
                color="blue",
                terminal=True,
                refresh=True,
                shell=shell,
                params=params,
                searchable=False,
            )
        if formula_names:
            shell, params = upgrade_all_shell(brew_path, formula_names, [], POST_RUN_CLEANUP)
            dropdown.item(
                f"Upgrade All Formulas ({len(formula_names)})",
                sfimage="arrow.up.circle",
                terminal=True,
                refresh=True,
                shell=shell,
                params=params,
                searchable=False,
            )
        if cask_names:
            shell, params = upgrade_all_shell(brew_path, [], cask_names, POST_RUN_CLEANUP)
            dropdown.item(
                f"Upgrade All Casks ({len(cask_names)})",
                sfimage="arrow.up.circle",
                terminal=True,
                refresh=True,
                shell=shell,
                params=params,
                searchable=False,
            )
        if excluded:
            names = ", ".join(sorted(p["name"] for p in excluded))
            dropdown.item(
                f"Excluded from Upgrade All: {names}",
                color="gray",
                tooltip="Set via the UPGRADE_ALL_EXCLUDE preference",
            )

    any_items = False
    if formulas:
        dropdown.separator()
        dropdown.item(f"Formulas ({len(formulas)}):", header=True)
        for f in formulas:
            add_package_item(
                dropdown, f, "formula", f["name"] in UPGRADE_ALL_EXCLUDE, brew_path
            )
        any_items = True
    if casks:
        dropdown.separator()
        dropdown.item(f"Casks ({len(casks)}):", header=True)
        for c in casks:
            add_package_item(
                dropdown, c, "cask", c["name"] in UPGRADE_ALL_EXCLUDE, brew_path
            )
        any_items = True
    if pinned:
        dropdown.separator()
        dropdown.item(f"Pinned ({len(pinned)}):", header=True)
        for f in pinned:
            add_pinned_item(dropdown, f, brew_path)
        any_items = True

    if not any_items:
        dropdown.separator()
        dropdown.item(
            "Everything up to date", sfimage="checkmark.circle", color="green"
        )

    emit(menu)


if __name__ == "__main__":
    main()
