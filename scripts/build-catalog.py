#!/usr/bin/env python3
"""Regenerate vee-catalog.json from the repo layout.

The folder structure IS the catalog (Vee's convention shape); this script adds
the curation layer: titles, summaries, tags, and a SHA-256 pin per plugin so an
install can be verified before it touches disk.

    scripts/build-catalog.py            # rewrite vee-catalog.json
    scripts/build-catalog.py --check    # exit 1 if it is out of date (CI)

Metadata comes from each plugin's own `<vee.*>` headers, so there
is exactly one place to edit: the plugin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "vee-catalog.json"

# Category folders, in the order Discover should show them.
CATEGORIES = ["System", "Developer", "Network", "Monitoring", "Productivity", "Showcase"]

STORE_NAME = "Vee Plugins by navbytes"
HOMEPAGE = "https://github.com/navbytes/vee-plugins"
MIN_MACOS = "26.0"

# Vee's HeaderParser matches <(xbar|swiftbar|vee).KEY> and switches on KEY alone —
# the namespace is decorative, so <vee.title> and <xbar.title> are the same tag.
# Key by KEY here too: this store writes <vee.*> throughout, and an imported
# plugin still carries <xbar.*>/<swiftbar.*> until it is converted.
TAG_RE = re.compile(r"<(?:xbar|swiftbar|vee)\.([a-zA-Z.]+)>(.*?)</(?:xbar|swiftbar|vee)\.\1>", re.S)
PLUGIN_RE = re.compile(r"^[\w.-]+\.(sh|py|ts|js|rb|pl|swift)$")


def read_tags(text: str) -> dict[str, str]:
    """Every `<ns.name>value</ns.name>` header in a source file, keyed by bare name."""
    return {name.lower(): val.strip() for name, val in TAG_RE.findall(text)}


def derive_tags(tags: dict[str, str], category: str) -> list[str]:
    """Search keywords: the category, plus what the trust headers reveal."""
    out = [category.lower()]
    if tags.get("network"):
        out.append("network")
    if tags.get("secrets"):
        out.append("secrets")
    if tags.get("surface") in ("both", "widget"):
        out.append("widget")
    if tags.get("filter") == "true":
        out.append("searchable")
    if tags.get("type") == "streamable":
        out.append("streaming")
    return list(dict.fromkeys(out))  # dedupe, keep order


def entry(path: Path) -> dict:
    rel = path.relative_to(ROOT).as_posix()
    category = rel.split("/")[0]
    source = path.read_bytes()
    tags = read_tags(source.decode("utf-8", "replace"))

    e = {
        "path": rel,
        "title": tags.get("title") or path.name,
        "category": category,
        "summary": tags.get("desc", ""),
        "author": tags.get("author", "Naveen Kumar"),
        "min_macos": MIN_MACOS,
        "sha256": hashlib.sha256(source).hexdigest(),
        "deprecated": False,
        "tags": derive_tags(tags, category),
    }
    # `surface` lets Discover flag a widget-only plugin without downloading it.
    if surface := tags.get("surface"):
        e["surface"] = surface
    return e


def plugins() -> list[Path]:
    found = []
    for category in CATEGORIES:
        d = ROOT / category
        if not d.is_dir():
            continue
        found += sorted(p for p in d.iterdir() if p.is_file() and PLUGIN_RE.match(p.name))
    return found


def build() -> dict:
    return {
        "vee_catalog": 1,
        "name": STORE_NAME,
        "homepage": HOMEPAGE,
        "plugins": [entry(p) for p in plugins()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify the manifest is current")
    args = ap.parse_args()

    # Plugins are dependency-free executables — no SDK file may be committed.
    if sdk_files := [p.relative_to(ROOT) for p in ROOT.rglob("*") if p.name in ("vee.py", "vee.ts") and ".claude" not in p.parts]:
        print(f"SDK files may not be committed (plugins are dependency-free): {sdk_files}", file=sys.stderr)
        return 1

    built = build()
    if not built["plugins"]:
        print("no plugins found — check CATEGORIES", file=sys.stderr)
        return 1

    text = json.dumps(built, indent=2) + "\n"
    if args.check:
        current = MANIFEST.read_text() if MANIFEST.exists() else ""
        if current != text:
            print("vee-catalog.json is stale — run scripts/build-catalog.py", file=sys.stderr)
            return 1
        print(f"vee-catalog.json is current ({len(built['plugins'])} plugins).")
        return 0

    MANIFEST.write_text(text)
    print(f"wrote {MANIFEST.relative_to(ROOT)} ({len(built['plugins'])} plugins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
