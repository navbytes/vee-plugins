# Brief for plugin authors (agents)

You are writing plugins for **navbytes/vee-plugins** — a curated, trustworthy
Vee plugin store. Repo root: `/Users/naveen/repos/vee-plugins`.

## Read these first (local files, authoritative)

- `~/repos/vee/docs/_content/plugin-authoring.md` — the format reference
- `~/repos/vee/docs/_content/json-output.md` — the JSON output format (**prefer this**)
- `~/repos/vee/docs/_content/trust-model.md` — `<vee.*>` declarations
- `~/repos/vee/docs/_content/preferences.md` — `<xbar.var>` typed settings
- `~/repos/vee/docs/_content/widgets.md` — only if your plugin has a widget
- `~/repos/vee/plugins/showcase/kitchen-sink.1m.sh` — house style, JSON format
- `~/repos/vee/docs/schemas/json-output.schema.json`, `widget-card.schema.json`

## Hard rules

1. **JSON output format** (`{"vee":1,...}`) unless the plugin is streaming or
   the text protocol is genuinely simpler. Build it with a heredoc or `python3
   -c`, never with `jq` (no dependency). **Escape strings properly** — data from
   `ps`, git branches, container names can contain `"` and `\`.
2. **Zero non-macOS-builtin dependencies.** bash/zsh, `/usr/bin/*`, `python3`
   (Apple ships it). If the plugin wraps an optional tool (docker, gh), it must
   detect its absence and print a helpful "not installed" row, never a stack
   trace or empty menu.
3. **Degrade gracefully, always.** No token / no network / no dependency / no
   data ⇒ a clear, actionable single row. Never a traceback in the menu bar.
4. **Honest `<vee.*>` trust declarations.** List every domain, every external
   binary, every path read/written, every secret. Declaring more than you use is
   fine; declaring less is a bug.
5. **Full metadata headers**: `<xbar.title>`, `<xbar.version>1.0`,
   `<xbar.author>Naveen Kumar`, `<xbar.author.github>navbytes`, `<xbar.desc>`,
   `<xbar.dependencies>`, `<xbar.abouturl>https://github.com/navbytes/vee-plugins`.
6. **Fast.** The whole run must finish well under 3 seconds. No unbounded loops,
   no `sleep` (except a streaming plugin's own tick), no network call without a
   timeout (`curl --max-time 4 -sS`).
7. **Never destructive without deliberation.** A row that kills a process or
   stops a container gets `"searchable": false` so a typed query + Return can
   never land on it.
8. **Comment for readers.** These are read before they are run. A header comment
   block saying what it does, what it touches, and what it needs. Then terse
   inline comments. Match the style of `kitchen-sink.1m.sh`.
9. **No executable bit.** Ship files 0644 — users chmod after reading.
10. **State between runs goes in `$SWIFTBAR_PLUGIN_CACHE_PATH`** (fall back to
    `${TMPDIR}` if unset). Every run is a fresh process.

## Verify before you report done

```sh
vee lint <path>          # must print "No lint findings." and exit 0
vee render <path>        # eyeball the tree it builds
bash -n <path>           # syntax (shell plugins)
```

`vee lint` **executes** the plugin, so it is also a smoke test. Iterate until
clean. Report the final `vee lint` output for each file you wrote.

## Gotchas that bite

- The first `|` on a text-protocol line starts params; a literal `|` in display
  text must be `\|`. (Not an issue in JSON — one more reason to use JSON.)
- Accessory sizing is `accessoryWidth` / `accessoryHeight` (JSON) and
  `accessoryw=` / `accessoryh=` (text). `progressw=`/`sparklinew=`/`chartw=` are
  deprecated — do not use them.
- Charts: `"chart": {"kind":"pie"|"donut"|"stackedbar","values":[...],"labels":[...]}`.
  Values must be finite, `>= 0`, at least one positive, max 8 segments.
- `progress` is a fraction `0…1` in JSON.
- Filename carries the interval: `name.INTERVAL.ext`.
- Widget mode: branch on `$VEE_TARGET` (`menu` vs `widget`) and print one
  `{"vee_widget":1,...}` object for `widget`.
