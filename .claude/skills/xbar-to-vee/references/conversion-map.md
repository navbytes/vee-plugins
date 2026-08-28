# Conversion map

## Output format

| Old | Vee |
|---|---|
| `echo "Title \| color=red"` | JSON `{"vee":1,"title":[{"text":"Title","color":"red"}]}` |
| `---` then rows | `"items": [...]` |
| `--Nested` / `----Deeper` | `"submenu": [ { "submenu": [...] } ]` |
| `\| bash=cmd param0=a param1=b` | `"shell":"cmd","params":["a","b"]` |
| `\| href=URL` | `"href":"URL"` |
| `\| refresh=true` | `"refresh":true` |
| `\| terminal=false` | `"terminal":false` |
| `\| color=… size=… font=…` | `"color"`, `"size"` (no `font` in JSON — keep the text protocol if a custom font matters) |
| `\| templateImage=` / `image=` base64 | prefer `"sfimage":"<SF Symbol name>"` |
| `\| length=20` truncation | truncate in the script; keep the label short |
| Manual `\|` and `\\` escaping | gone — JSON strings need none |
| `\| alternate=true` on the *next* row | `"alternate": { … }` nested in the primary row |
| `\| dropdown=false` | `"visibleOn": ["menu"]` — or drop it, `visibleOn` is finer-grained |
| Unicode block-bar progress (`████░░░░`) | `"progress": 0.5` — a real capsule bar |
| ASCII sparkline (`▁▂▃▅▇`) | `"sparkline": [1,2,3,5,7]` |
| Hand-drawn percentage breakdown | `"chart": {"kind":"donut","values":[…],"labels":[…]}` |
| A "Settings" row opening a config file | `<vee.var>` typed preferences — Vee generates the form |

Sizing accessories: `accessoryWidth` / `accessoryHeight` (JSON),
`accessoryw=` / `accessoryh=` (text). The old `progressw=`, `sparklinew=`,
`chartw=` spellings are deprecated — do not emit them.

## Metadata

**Rename every header to the `<vee.*>` namespace** — `<xbar.title>` becomes
`<vee.title>`, `<swiftbar.type>` becomes `<vee.type>`, and so on. Vee's
`HeaderParser` matches `<(xbar|swiftbar|vee).KEY>` and switches on `KEY` alone,
so this changes nothing functionally; it is what marks a plugin as converted
rather than merely copied. Note the cost: a `<vee.*>`-only plugin no longer runs
on xbar or SwiftBar, which read only their own namespaces. If the user wants to
keep it portable to those tools, say so and leave the original spellings.

The **environment variables do not follow this rename.**
`SWIFTBAR_PLUGIN_CACHE_PATH` and `SWIFTBAR_PLUGIN_DATA_PATH` have no `VEE_*`
equivalent in the runtime and must keep their names. `VEE_PLUGIN_PATH`,
`VEE_PLUGIN_ID`, `VEE_TARGET`, and `VEE_CONTROL_VALUE` are Vee-native.

Add on conversion:

| Add | Why |
|---|---|
| `<vee.network>`, `<vee.secrets>`, `<vee.exec>`, `<vee.filesystem.read/write>` | The trust summary shown at install. Derive from the code, not the author's claims. |
| `<vee.filter>true</vee.filter>` | Any plugin with more than ~15 rows or deep submenus — gives it ⌘F search. |
| `<vee.shortcut>cmd+shift+…</vee.shortcut>` | Only when the user asks; it grabs a global hotkey. |
| `<vee.surface>both</vee.surface>` | When the plugin's headline number deserves a real widget card. |
| `<vee.timeout>` | Only if the original genuinely needs more than 30s — usually the right fix is to make it faster. |

## Dead or changed macOS calls

| Old call | Status | Replacement |
|---|---|---|
| `/usr/bin/python` | removed (Python 2) | `/usr/bin/python3` |
| `airport -I` (`.../Apple80211.framework/.../airport`) | removed/neutered | `system_profiler SPAirPortDataType`, `ipconfig getsummary en0`, `networksetup -getairportnetwork en0` |
| `pmset -g batt` parsing | fine | still fine; `system_profiler SPPowerDataType -detailLevel mini` for health |
| `top -l 2` | slow | `ps -Aceo pcpu,comm -r` or a single `top -l 1` |
| `netstat -an` for listeners | deprecated output | `lsof -nP -w -iTCP -sTCP:LISTEN` |
| `system_profiler` without `-detailLevel mini` | very slow | add `-detailLevel mini`, call once, parse everything from that one run |
| `osascript` for volume/mute | fine | still the way; wire to `toggle=` / `slider=` and read `$VEE_CONTROL_VALUE` |
| `terminal-notifier` | third-party | `open "swiftbar://notify?plugin=$VEE_PLUGIN_ID&title=…"` |
| `curl` with no timeout | hangs the menu | add `--max-time 4 -sS` |

## Environment

| Old | Vee |
|---|---|
| `$BitBar*` / `$XBAR*` | still injected for compatibility; `$VEE_*` is native where it exists |
| assumed state between runs | `$SWIFTBAR_PLUGIN_CACHE_PATH` (fall back to `${TMPDIR:-/tmp}`) |
| hardcoded plugin path | `$VEE_PLUGIN_PATH` |
| n/a | `$VEE_TARGET` — `menu` or `widget`; branch to emit a widget card |
| n/a | `$VEE_CONTROL_VALUE` — the new value on a `toggle=`/`slider=` re-invocation |
| dark-mode detection hacks | `$XBARDarkMode` / `$OS_APPEARANCE` |

## Things that have no Vee equivalent — say so, do not fake one

- `webview=` / embedded HTML UI: Vee draws natively. Re-express as rows, charts,
  and popovers, and tell the user what changed.
- Custom base64 images: use SF Symbols; if the image is essential, keep it, but
  prefer a symbol.
- Plugins that shell out to a GUI helper app the user does not have: guard the
  call and print a "not installed" row.
