# Contributing

This is a **curated** store, not an open dumping ground. The whole reason it
exists is that the public xbar catalog is full of unmaintained scripts nobody
has read. Every plugin here is reviewed, runs on current macOS, and declares
what it touches.

## The bar

A plugin is accepted when all of these hold:

1. **It is useful more than once.** Something you would actually leave in your
   menu bar, not a format demo.
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
10. **`vee lint` is clean.**

New plugins should use Vee's [JSON output format](https://vee.navbytes.io/guide/json-output/)
(`{"vee":1,…}`) — it removes the entire class of `|`-quoting and escaping bugs.
The text protocol is still right for a streaming plugin.

## The loop

```sh
vee dev ./Category/plugin.30s.sh   # re-runs and repaints on every save
vee lint ./Category/plugin.30s.sh  # must exit 0
vee render ./Category/plugin.30s.sh
python3 scripts/build-catalog.py   # regenerate vee-catalog.json
```

`vee-catalog.json` is **generated** — never hand-edit it. Metadata comes from
each plugin's own `<xbar.*>` / `<vee.*>` headers, so there is one place to edit:
the plugin. CI fails if the manifest is stale.

## Adding a plugin

1. Drop it in the right category folder: `System/`, `Developer/`, `Network/`,
   `Monitoring/`, `Productivity/`. The folder name **is** the category Discover
   shows. A new category means adding it to `CATEGORIES` in
   `scripts/build-catalog.py`.
2. Name it `name.INTERVAL.ext` — `disk.10m.sh`, `ports.30s.sh`. No interval means
   run-on-demand only.
3. Fill in the metadata headers. `<xbar.desc>` is required — CI rejects a plugin
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
