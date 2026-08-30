# Contributing

This is a **curated** store, not an open dumping ground. The whole reason it
exists is that the public xbar catalog is full of unmaintained scripts nobody
has read. Every plugin here is reviewed, runs on current macOS, and declares
what it touches.

## The bar

A plugin is accepted when all of these hold:

1. **It is useful more than once.** Something you would actually leave in your
   menu bar, not a format demo. There is exactly one exception in the store,
   `Showcase/controls.py`: every control the plugin format supports, in one
   file, so Discover has a single way to browse them. Kept singular on
   purpose — a second one goes in [`demo/`](demo/), which Discover never
   lists.
2. **No dependencies beyond what macOS ships.** bash/zsh, `/usr/bin/*`, and
   `python3`. Wrapping an optional tool (`git`, `docker`, `gh`) is fine — it must
   detect the tool's absence and say so in a row. There are exactly two
   exceptions in the store, both because the dependency buys something no
   shipped tool can: `clipboard.swift` needs the Xcode Command Line Tools,
   since reading `NSPasteboard`'s change counter and its concealed-type
   markers — what keeps a clipboard manager from recording your password
   manager's output — has no shell equivalent; and `litellm-cost.90s.ts` needs
   Node 24+, since macOS ships no JavaScript runtime. A new plugin clears this
   bar only by showing the dependency buys something a shipped tool genuinely
   cannot do.
3. **It degrades gracefully.** No token, no network, no dependency, no data:
   each one gets a clear, actionable row. A traceback in the menu bar is a bug.
4. **Its `<vee.*>` declarations are honest.** Every domain, binary, path, and
   secret. Declaring more than you use is fine; declaring less is a rejection.
5. **It finishes comfortably within Vee's execution budget** and never blocks
   on an unbounded call — no long-running work outside streaming plugins.
   Every `curl` carries `--max-time`.
6. **Destructive rows are `"searchable": false`** so a typed query plus Return
   can never land on one.
7. **State lives in a per-plugin directory, and which one depends on what
   losing it costs.** Every run is a fresh process, so anything that must
   survive goes to disk. Use `$SWIFTBAR_PLUGIN_CACHE_PATH` for state you can
   simply regenerate — a sparkline history, a cached API response. Use
   `$SWIFTBAR_PLUGIN_DATA_PATH` when losing it breaks something: `caffeine`
   records the PID of a process it spawned, and a cache eviction there orphans
   a real `caffeinate` the plugin can then never stop. Fall back down the chain
   (`DATA` → `CACHE` → `${TMPDIR:-/tmp}`) so the plugin still works if a path is
   unset. `$TMPDIR` alone is not state: macOS prunes it, and it differs per
   login session.
8. **It is readable.** These files are read before they are run — a header
   comment saying what it does, what it touches, and what it needs, then terse
   inline comments.
9. **It ships non-executable** (`0644`). Users `chmod +x` after reading.
10. **Every header uses the `<vee.*>` namespace.** `<vee.title>`, `<vee.desc>`,
    `<vee.var>`, `<vee.type>`, not the `<xbar.*>` / `<swiftbar.*>` spellings.
    Vee's `HeaderParser` matches `<(xbar|swiftbar|vee).KEY>` and switches on
    `KEY` alone, so all three are read identically — this is a house convention,
    not a functional requirement. It costs portability to xbar and SwiftBar,
    which only read their own namespaces; that is a deliberate trade, since this
    is a Vee store and much of what these plugins do has no xbar equivalent
    anyway.

    The **environment variables are a separate matter** and keep their
    `SWIFTBAR_` names: `SWIFTBAR_PLUGIN_CACHE_PATH` and
    `SWIFTBAR_PLUGIN_DATA_PATH` have no `VEE_*` equivalent in the runtime.
    `VEE_PLUGIN_PATH`, `VEE_PLUGIN_ID`, `VEE_TARGET`, and `VEE_CONTROL_VALUE`
    are Vee-native — use those where they exist.
11. **`vee lint` is clean.**

## Build the menu directly — no SDK

Every plugin here is a **dependency-free executable**: it prints the
`key=value` xbar/SwiftBar text protocol, or Vee's
[JSON output format](https://vee.navbytes.io/guide/json-output/), straight to
stdout — `print()`/`console.log()` (text protocol) or
`json.dumps()`/`JSON.stringify()` (JSON), no imports beyond the standard
library. `python3 System/system-vitals.10s.py` or `node
Monitoring/litellm-cost.90s.ts` just runs, unmodified, outside Vee too — an
editor's Run button, a debugger.

Prefer the JSON output format for a new plugin — no quoting or escaping games.
Reach for the text protocol when the plugin streams, or needs `font=` /
`length=`, which JSON cannot express; in that case a param value containing
whitespace, `|`, or `\` needs manual quoting, and a literal `|` or `\` in
display text needs escaping as `\|` / `\\`, or Vee's parser reads it as the
params delimiter — see [`demo/`](demo/) for what this costs on a plugin that
exercises every option in the format. **No SDK file (`vee.py`/`vee.ts`) may be
committed to this repo** — `scripts/build-catalog.py` rejects one if it finds
one.

## The loop

```sh
vee dev ./Category/plugin.30s.sh   # re-runs and repaints on every save
vee lint ./Category/plugin.30s.sh  # must exit 0
vee render ./Category/plugin.30s.sh
python3 scripts/build-catalog.py   # regenerate vee-catalog.json
```

`vee-catalog.json` is **generated** — never hand-edit it. Metadata comes from
each plugin's own `<vee.*>` headers, so there is one place to edit:
the plugin. CI fails if the manifest is stale.

## Adding a plugin

1. Drop it in the right category folder: `System/`, `Developer/`, `Network/`,
   `Monitoring/`, `Productivity/`. The folder name **is** the category Discover
   shows. A new category means adding it to `CATEGORIES` in
   `scripts/build-catalog.py`. `Showcase/` is deliberately capped at the one
   demo plugin (rule 1) — it is not open for additions.
2. Name it `name.INTERVAL.ext` — `disk.10m.sh`, `ports.30s.sh`. No interval means
   run-on-demand only.
3. Fill in the metadata headers. `<vee.desc>` is required — CI rejects a plugin
   without one, because it is what Discover shows on the card.
4. `python3 scripts/build-catalog.py`, then commit the plugin and the manifest
   together.

## Porting a plugin from xbar / BitBar / SwiftBar

Do not paste one in unread. This repo ships a Claude Code skill that audits
first and converts second:

```
/xbar-to-vee <path or URL>
```

It reads every line for exfiltration, credential theft, remote code execution,
and destructive commands, reports the findings, and refuses to convert anything
it judges hostile. See `.claude/skills/xbar-to-vee/`.

## Security

Found something wrong with a plugin here? Open an issue, or email the address in
the repo profile for anything you would rather not file publicly. Plugins run
un-sandboxed with your full user privileges — that is exactly why this store is
curated and why every file is meant to be read before it is run.
