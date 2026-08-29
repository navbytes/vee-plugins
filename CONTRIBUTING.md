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
   detect the tool's absence and say so in a row. There is exactly one exception
   in the store, `clipboard.swift`, which needs the Xcode Command Line Tools:
   reading `NSPasteboard`'s change counter and its concealed-type markers is
   what keeps a clipboard manager from recording your password manager's
   output, and no shell tool exposes them. A new plugin clears this bar only by
   showing the dependency buys something a shipped tool genuinely cannot do.
3. **It degrades gracefully.** No token, no network, no dependency, no data:
   each one gets a clear, actionable row. A traceback in the menu bar is a bug.
4. **Its `<vee.*>` declarations are honest.** Every domain, binary, path, and
   secret. Declaring more than you use is fine; declaring less is a rejection.
5. **It finishes in under ~3 seconds** and never blocks on an unbounded call.
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

## Build the menu with the SDK

Every plugin here builds its output with Vee's own SDK rather than formatting
lines by hand. The exception is `clipboard.swift`: there is no Swift SDK, so it
formats its own output and does the quoting itself. If you write a Swift plugin,
you are in the same position — and the escaping rules below are yours to get
right rather than the SDK's.

```python
from vee import JSONMenu          # or Menu, for the text protocol
```
```ts
import { Menu, Gauge } from "./vee.ts";
```

This is not stylistic. The SDK owns quoting, escaping and number formatting —
`item("a|b and back\slash")` emits `a\|b and back\\slash`, which is exactly the
corruption that silently broke rows in a hand-formatted plugin in this repo. It
also **rejects unknown options at runtime with a did-you-mean**, so a typo'd or
misused parameter fails loudly instead of rendering nothing at all. Converting
this store surfaced a real bug that way: a `submenu=[…]` passed as an option
rather than through `.submenu()` had been silently dropping a whole subtree.

Prefer `JSONMenu` (the structured-JSON format). Reach for the text-protocol
`Menu` when the plugin streams, or when it needs `font=` / `length=`, which the
JSON format cannot express.

### The SDK, and why it is still vendored here

**Vee 0.6.2 and later provide the SDK when they run a plugin**, so a plugin
installed from Discover needs nothing beside it. `vee.py` / `vee.ts` are still
vendored into each category folder for two reasons: a plugin here has to run
under older Vee versions too, and a vendored copy is what lets you run one
directly — `python3 System/system-vitals.10s.py`, an editor's Run button, a
debugger — without going through Vee at all.

A copy beside a plugin always takes precedence over Vee's own, so the vendored
files decide which SDK the plugins in this repository run against.

For your own plugins folder, a copy is optional on 0.6.2+ and required below it:

```sh
vee sdk py --out ~/Library/Application\ Support/Vee/plugins
vee sdk ts --out ~/Library/Application\ Support/Vee/plugins   # only if you run a TS plugin
```

Vee's own plugin discovery skips `vee.ts` / `vee.py` by name, so they sit in the
plugins folder without ever being run as plugins.

Regenerate the repo's vendored copies (all category folders at once) with:

```sh
scripts/sync-sdk.sh
```

Never hand-edit a vendored `vee.py` / `vee.ts`, and never import
`@navbytes/vee` from npm — that specifier does not resolve in the plugins
folder, which is the only place it has to.

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
   `scripts/build-catalog.py`.
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
