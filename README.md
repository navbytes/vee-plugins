# Vee Plugins

A **curated** plugin store for [Vee](https://github.com/navbytes/vee), the native
macOS menu-bar script runner.

Vee ships pointing at the public `xbar-plugins` catalog. That catalog is large,
old, and mostly unreviewed — plugins run un-sandboxed with your full user
privileges, and a lot of what is in there has not been touched in years or no
longer works on current macOS. This store is the opposite trade: **fifteen
plugins, all read, all current, all declaring exactly what they touch.**

## Add it to Vee

**Preferences → Stores → Add store…**, choose **GitHub**, enter:

| Field | Value |
| --- | --- |
| Owner | `navbytes` |
| Repo | `vee-plugins` |

They appear in **Discover** under this store, installing through Vee's normal
trust gate. Everything here is public — no token needed.

Or grab one file directly:

```sh
cd ~/Library/Application\ Support/Vee/plugins
curl -O https://raw.githubusercontent.com/navbytes/vee-plugins/main/System/system-vitals.10s.py
# read it, then:
chmod +x system-vitals.10s.py
```

Plugins ship **non-executable on purpose** — you read the source, then you
`chmod +x`. That is the deal this store is built around.

### The SDK

Every plugin here builds its menu with Vee's own SDK rather than formatting text
by hand. **Vee 0.6.2 and later provide it when they run a plugin**, so there is
nothing to install — a plugin from Discover, or one you curl into place, just
runs.

On **Vee 0.6.1 or earlier** the SDK had to sit beside the plugins, and without it
a plugin failed with `ModuleNotFoundError: No module named 'vee'`. If you are on
an older Vee, or you want to run a plugin with a bare `python3`/`node` outside
Vee — an editor's Run button, a debugger — put a copy in the folder:

```sh
vee sdk py --out ~/Library/Application\ Support/Vee/plugins   # all but one plugin
vee sdk ts --out ~/Library/Application\ Support/Vee/plugins   # only for litellm-cost
```

A copy beside a plugin always takes precedence over Vee's own, so adding one
changes nothing about how the plugin behaves. Vee's plugin discovery skips
`vee.py` / `vee.ts` by name, so they sit in the folder without being run as
plugins.

Why bother: the SDK owns quoting and escaping. Given a path containing a space
it emits `param1="/Users/you/Library/Application Support/Vee/plugins/pomodoro.py"`
correctly quoted, and given `a|b` it emits `a\|b` — the exact corruption that
silently broke rows here before. It also rejects unknown options at runtime, which
caught a `submenu` being passed as a parameter and silently dropping an entire
subtree.

## What's in it

### System

| Plugin | Every | What it does |
| --- | --- | --- |
| [`system-vitals.10s.py`](System/system-vitals.10s.py) | 10s | CPU, memory pressure, swap, uptime. Keeps a rolling 40-sample history on disk so the sparkline is real, not decorative. Top-5 CPU and memory processes each get their own bar. **Also a desktop widget.** |
| [`battery.5m.py`](System/battery.5m.py) | 5m | Charge, time remaining, cycle count, condition, and capacity health as a donut. One `system_profiler` call, timeout-guarded. Says "no battery" cleanly on a desktop Mac. **Also a desktop widget.** |
| [`disk-space.10m.py`](System/disk-space.10m.py) | 10m | Free space per volume, with `/` and `/System/Volumes/Data` collapsed into one honest boot-volume row. The "biggest folders in ~" scan is timeout-guarded and skips itself rather than hanging your menu. |
| [`audio.10s.py`](System/audio.10s.py) | 10s | Output device, plus a **live volume slider and mute toggle in the menu row** — drag it and the volume moves. Switch output device from a submenu. |
| [`caffeine.1m.py`](System/caffeine.1m.py) | 1m | Keep the Mac awake indefinitely or for a timed session, with a countdown in the menu bar. Two modes: hold the display awake too, or let it sleep while the system stays up. Finds `caffeinate` processes it didn't start and lets you adopt or stop each one individually. |

### Developer

| Plugin | Every | What it does |
| --- | --- | --- |
| [`git-repos.2m.py`](Developer/git-repos.2m.py) | 2m | Every repo under `GIT_ROOTS` bucketed into uncommitted / unpushed / clean, with branch and `+N ~M -K` counts. **⌘⇧G opens a search panel** — type a repo name to jump to it. Never touches the network. |
| [`dev-ports.30s.py`](Developer/dev-ports.30s.py) | 30s | What is listening on your dev ports, with open-in-browser, copy-URL, and kill actions. The kill row is `searchable: false` so a typed query can never land on it. |
| [`github.5m.py`](Developer/github.5m.py) | 5m | PRs waiting on your review, your own open PRs, unread notifications. Uses the `gh` CLI if you have it (no token to manage), falls back to a Keychain-stored token, and falls back again to cached data with a "stale" banner if the network is down. |

### Network & Monitoring

| Plugin | Every | What it does |
| --- | --- | --- |
| [`network.30s.py`](Network/network.30s.py) | 30s | Active interface, Wi-Fi signal as a real quality bar, addresses / gateway / DNS (click any to copy), and a latency sparkline. Public-IP lookup is **opt-in and off by default**. |
| [`uptime.5m.py`](Monitoring/uptime.5m.py) | 5m | Health checks for **your own** endpoints, run in parallel. Per-target response-time sparklines. Ships with no targets — it will never contact anything you did not configure. **Also a desktop widget board.** |
| [`litellm-cost.90s.ts`](Monitoring/litellm-cost.90s.ts) | 90s | Daily LLM spend against your budget from a [LiteLLM](https://litellm.ai) proxy: a capacity bar broken down by model, per-model spend and error counts, a 7-day trend, and cache savings. Caches the last good response and says so when the proxy is unreachable. **Also a desktop widget gauge. Needs Node 24+.** |

### Productivity

| Plugin | Every | What it does |
| --- | --- | --- |
| [`pomodoro.py`](Productivity/pomodoro.py) | streams | A focus timer that **streams** — the countdown ticks once a second instead of waiting on a refresh interval. Focus/break phases, daily tally, one-click controls, and a notification when a phase ends. |
| [`worldclock.1m.py`](Productivity/worldclock.1m.py) | 1m | Your team's timezones with a green/yellow/grey "can I message them right now" indicator, a meeting-overlap planner, and click-to-copy ISO timestamps. |
| [`clipboard.swift`](Productivity/clipboard.swift) | streams | Clipboard history with **full-text search over every entry** (⌘⌃V from anywhere), pins, and ⌥ to pin/unpin in place. Skips anything a password manager marks concealed, and honours macOS 15.4's pasteboard-privacy gate instead of nagging you. **Needs the Xcode Command Line Tools.** |

### Showcase

The one deliberate exception to "useful more than once" — a reference, not a
utility. See [`demo/`](demo/) for the full set: the same menu again with no
SDK and in TypeScript, all four verified byte-identical.

| Plugin | Every | What it does |
| --- | --- | --- |
| [`controls.py`](Showcase/controls.py) | on demand | Every control the plugin format supports — sliders, toggles, progress bars, pie/donut/stacked-bar charts, SF Symbols, Markdown, submenus — in one file, built with the SDK. |

Most of these have settings — open the plugin's Settings in Vee's Plugin Manager.
`GIT_ROOTS`, `UPTIME_TARGETS`, `TIMEZONES`, and `PORT_RANGES` are the ones worth
setting first.

## The rules every plugin here follows

1. **Nothing beyond what macOS ships**, plus the vendored SDK. `python3` and
   `/usr/bin/*`; optional tools (`git`, `gh`, `docker`) are detected, never
   assumed. Two deliberate exceptions, both because the dependency buys
   something no shipped tool can: `clipboard.swift` needs the Xcode Command Line
   Tools, since reading `NSPasteboard`'s concealed-type markers is what stops it
   recording your passwords; and `litellm-cost.90s.ts` needs Node 24+.
2. **Honest `<vee.*>` declarations.** Every domain, binary, path, and secret the
   plugin touches is declared. Declaring more than you use is fine; declaring
   less is a bug that gets a plugin rejected.
3. **No surprise network calls.** Only `github.5m.py` (api.github.com),
   `network.30s.py` (api.ipify.org, **opt-in**), and `uptime.5m.py` (targets
   *you* configure) go outbound at all. The clipboard plugin reads everything
   you copy and sends it **nowhere** — it writes to a `0700` directory under
   `~/Library/Caches` and that is the whole of it. There is no telemetry
   anywhere.
4. **Graceful degradation.** No token, no network, no dependency, no data — each
   gets one clear row. A traceback in your menu bar is a bug.
5. **Bounded.** Every `curl` has `--max-time`, every scan has a cap, every
   subprocess has a timeout.
6. **Destructive rows are `searchable: false`** so a typed query plus Return
   cannot fire one.
7. **Readable.** Each file opens with what it does, what it touches, and what it
   needs. They are written to be read.

Every plugin is verified with `vee lint` and audited against
[the security checklist](.claude/skills/xbar-to-vee/references/audit-checklist.md)
this repo publishes.

## Porting a plugin from xbar / BitBar / SwiftBar

There are thousands of old plugins out there and most have never been read by
anyone. This repo ships a Claude Code skill that **audits first, converts
second**:

```
/xbar-to-vee <path or URL>
```

It reads every line for exfiltration, credential theft, remote code execution,
and destructive commands, reports what it found with line numbers, and **refuses
to convert anything it judges hostile** — it will not hand you a "cleaned"
version of a malicious script. What it does convert comes out in Vee's modern
JSON format with honest trust headers, hardcoded keys moved to the Keychain, and
dead macOS calls (`airport -I`, `/usr/bin/python`, `netstat`) modernized.

The skill lives in [`.claude/skills/xbar-to-vee/`](.claude/skills/xbar-to-vee/)
and works on any clone of this repo. To use it anywhere:

```sh
ln -s "$PWD/.claude/skills/xbar-to-vee" ~/.claude/skills/xbar-to-vee
```

## The catalog manifest

[`vee-catalog.json`](vee-catalog.json) is **generated** — never hand-edited. It
carries a title, summary, tags, and a **SHA-256 pin** per plugin, so Vee verifies
a download against the manifest before writing it to disk.

```sh
python3 scripts/build-catalog.py           # regenerate
python3 scripts/build-catalog.py --check   # CI: fail if stale
```

Metadata comes from each plugin's own `<vee.*>` headers, so there is
exactly one place to edit: the plugin. CI additionally checks shell and Python
syntax, shellcheck, that every manifest path resolves, and that nothing ships
with an executable bit.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The bar is "useful more than once,
readable, honest, and bounded" — a plugin that only demonstrates a format
feature belongs in Vee's own `plugins/showcase/`, not here. `Showcase/controls.py`
is the one deliberate exception, kept singular on purpose; anything more
belongs in [`demo/`](demo/) instead, which Discover never lists.

## Security

These plugins run un-sandboxed with your full user privileges, exactly like every
other Vee/xbar plugin. Curation is a real control, not a guarantee — read the
source. Found a problem? Open an issue.

## License

MIT — see [LICENSE](LICENSE).
