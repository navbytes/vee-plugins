#!/usr/bin/env python3
#
# github.5m.py — pull requests waiting on you.
#
# Two auth paths, tried in order:
#   (a) the `gh` CLI, if installed and authenticated (`gh auth status`) — no
#       token to manage, gh already has one in the Keychain/config.
#   (b) a GITHUB_TOKEN preference, used against api.github.com via urllib
#       (never curl — see the note above api_get for why).
# Neither available -> one clear row explaining both options, never a
# traceback in the menu bar. This is the graceful-degradation showcase.
#
# The three lookups (PRs awaiting your review, your open PRs, unread
# notification count) run concurrently — each is its own network round
# trip, and doing them one after another would blow the "well under 3s"
# budget for a menu bar refresh.
#
# The last successful result is cached to $SWIFTBAR_PLUGIN_CACHE_PATH; if a
# run's network calls fail outright, the cached menu is shown again with a
# "stale" banner instead of an error screen.
#
# <vee.title>GitHub PRs</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>PRs waiting on your review, your own open PRs, and unread notifications.</vee.desc>
# <vee.dependencies>python3</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# <vee.var>string(GITHUB_TOKEN=): Personal access token (needs "repo"/"notifications" read access). Not needed if the gh CLI is installed and logged in.</vee.var>
#
# Trust declarations (advisory, never enforced):
# <vee.network>api.github.com</vee.network>
# <vee.secrets>GITHUB_TOKEN</vee.secrets>
# <vee.exec>gh</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/github.cache.json</vee.filesystem.write>

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from vee import JSONMenu

LIMIT = 10
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
CACHE_DIR = os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH") or os.environ.get("TMPDIR", "/tmp")
CACHE_FILE = os.path.join(CACHE_DIR, "github.cache.json")


def emit(menu):
    menu.print()
    sys.exit(0)


def run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


# ---------------------------------------------------------------------------
# Path (a): gh CLI
# ---------------------------------------------------------------------------
def gh_authenticated():
    code, _ = run(["gh", "auth", "status"], timeout=8)
    return code == 0


def gh_search(extra_flags):
    code, out = run(
        ["gh", "search", "prs", *extra_flags, "--state", "open",
         "--json", "number,title,url,repository,author,createdAt,isDraft",
         "--limit", str(LIMIT)],
        timeout=10,
    )
    if code != 0:
        return None
    try:
        items = json.loads(out)
    except ValueError:
        return None
    return [
        {
            "number": it["number"],
            "title": it["title"],
            "url": it["url"],
            "repo": it["repository"]["nameWithOwner"],
            "author": it["author"]["login"],
            "created_at": it["createdAt"],
            "draft": it.get("isDraft", False),
        }
        for it in items
    ]


def gh_notifications_count():
    code, out = run(["gh", "api", "notifications", "--jq", "length"], timeout=8)
    if code != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Path (b): GITHUB_TOKEN against the search API
#
# Uses urllib.request (stdlib) instead of shelling out to curl: the
# Authorization header is set on an in-process Request object and never
# appears in a subprocess argv, where any other process running as this
# user could read it via `ps -Aeo args`. This also drops curl as a
# dependency for this path entirely.
# ---------------------------------------------------------------------------
def api_get(path_and_query):
    url = "https://api.github.com" + path_and_query
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return None


def token_search(query):
    q = "/search/issues?" + urllib.parse.urlencode({"q": query, "per_page": LIMIT})
    data = api_get(q)
    if data is None:
        return None
    items = data.get("items", [])
    out = []
    for it in items:
        repo_url = it.get("repository_url", "")
        repo = "/".join(repo_url.rstrip("/").split("/")[-2:]) if repo_url else "?"
        out.append(
            {
                "number": it["number"],
                "title": it["title"],
                "url": it["html_url"],
                "repo": repo,
                "author": it.get("user", {}).get("login", "?"),
                "created_at": it["created_at"],
                "draft": it.get("draft", False),
            }
        )
    return out


def token_notifications_count():
    data = api_get("/notifications")
    return len(data) if isinstance(data, list) else None


# ---------------------------------------------------------------------------
# Shared: cache, formatting, menu building
# ---------------------------------------------------------------------------
def load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_cache(data):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass  # cache is best-effort; a read-only cache dir shouldn't break the menu


def age_str(iso):
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return "unknown age"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def add_pr_item(section, pr, is_review_section):
    # pr may have come back from disk cache (see load_cache) rather than a
    # fresh API response, so its "url" is untrusted: only ever emit it as an
    # href when it is actually a github.com link, never whatever a
    # truncated/hand-edited/world-writable cache file happens to contain.
    url = pr.get("url", "")
    if not url.startswith("https://github.com/"):
        return False
    if is_review_section:
        color, sfimage = "orange", "eye"
    else:
        color, sfimage = ("gray", "pencil") if pr.get("draft") else ("green", "checkmark.circle")
    section.item(
        f"{pr.get('repo', '?')} #{pr.get('number', '?')} {pr.get('title', '(untitled)')}",
        href=url,
        color=color,
        sfimage=sfimage,
        tooltip=f"by {pr.get('author', '?')} · opened {age_str(pr.get('created_at'))}",
    )
    return True


def add_section(dropdown, header_text, prs, is_review_section, empty_text):
    dropdown.item(header_text, header=True)
    any_rows = False
    for pr in prs[:LIMIT]:
        if add_pr_item(dropdown, pr, is_review_section):
            any_rows = True
    if not any_rows:
        dropdown.item(empty_text, color="gray", disabled=True)


def build_menu(review, mine, notif_count, stale_since=None):
    review_count = len(review)
    if review_count > 0:
        title_text, title_color = f"{review_count} to review", "red"
    else:
        title_text, title_color = "Clear", "green"

    menu = JSONMenu()
    menu.title(title_text, sfimage="chevron.left.forwardslash.chevron.right", color=title_color)
    dropdown = menu.dropdown

    if stale_since is not None:
        dropdown.item(
            f"stale — last updated {time.strftime('%H:%M', time.localtime(stale_since))}",
            color="orange",
            sfimage="exclamationmark.triangle",
            disabled=True,
        )
        dropdown.separator()

    add_section(dropdown, "Waiting on your review", review, True, "Nothing waiting on your review")
    dropdown.separator()
    add_section(dropdown, "Your open PRs", mine, False, "You have no open PRs")

    if notif_count is not None:
        dropdown.separator()
        dropdown.item(
            f"{notif_count} unread notification{'s' if notif_count != 1 else ''}",
            href="https://github.com/notifications",
            sfimage="bell.badge" if notif_count else "bell",
            color="blue" if notif_count else "gray",
        )

    dropdown.separator()
    dropdown.item("Refresh", refresh=True, sfimage="arrow.clockwise")

    return menu


def emit_no_auth():
    menu = JSONMenu()
    menu.title("GitHub", sfimage="chevron.left.forwardslash.chevron.right", color="gray")
    dropdown = menu.dropdown
    dropdown.item("Not connected to GitHub", color="gray")
    dropdown.separator()
    dropdown.item(
        "Option A: install the gh CLI and run `gh auth login`", href="https://cli.github.com"
    )
    dropdown.item(
        "Option B: set a GITHUB_TOKEN in this plugin's preferences",
        href="https://github.com/settings/tokens",
    )
    emit(menu)


def emit_stale_or_error():
    cached = load_cache()
    if cached:
        emit(
            build_menu(
                cached.get("review") or [],
                cached.get("mine") or [],
                cached.get("notif"),
                stale_since=cached.get("ts") or time.time(),
            )
        )
    menu = JSONMenu()
    menu.title("GitHub ⚠", sfimage="chevron.left.forwardslash.chevron.right", color="red")
    menu.dropdown.item("Couldn't reach the GitHub API", color="gray")
    emit(menu)


def main():
    review = mine = notif = None
    attempted = False  # did we actually have a usable auth path to try?

    # The auth check and the two searches are independent network calls, so
    # run all four (auth + review + mine + notifications) at once — doing
    # them one after another would multiply GitHub's round-trip latency by 4.
    if shutil.which("gh"):
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_auth = ex.submit(gh_authenticated)
            f_review = ex.submit(gh_search, ["--review-requested=@me"])
            f_mine = ex.submit(gh_search, ["--author=@me"])
            f_notif = ex.submit(gh_notifications_count)
            authed = f_auth.result()
            if authed:
                attempted = True
                review, mine, notif = f_review.result(), f_mine.result(), f_notif.result()

    if not attempted and GITHUB_TOKEN:
        attempted = True
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_review = ex.submit(token_search, "is:open is:pr review-requested:@me")
            f_mine = ex.submit(token_search, "is:open is:pr author:@me")
            f_notif = ex.submit(token_notifications_count)
            review, mine, notif = f_review.result(), f_mine.result(), f_notif.result()

    if not attempted:
        emit_no_auth()
        return

    if review is None and mine is None:
        emit_stale_or_error()  # a real path was tried and failed
        return

    review, mine = review or [], mine or []
    save_cache({"review": review, "mine": mine, "notif": notif, "ts": time.time()})
    emit(build_menu(review, mine, notif))


if __name__ == "__main__":
    main()
