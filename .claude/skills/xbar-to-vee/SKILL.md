---
name: xbar-to-vee
description: Convert an xbar, BitBar, SwiftBar, or Argos plugin to a modern Vee plugin, with a mandatory security audit first. Use when the user wants to port, convert, migrate, adopt, modernize, or "bring over" a menu-bar plugin from xbar/BitBar/SwiftBar, when they paste a plugin script and ask what it does or whether it is safe, or when they type /xbar-to-vee. Always audits for exfiltration, credential theft, destructive commands, and untrusted remote code before converting — and refuses to convert a plugin it judges hostile.
---

# xbar / BitBar / SwiftBar → Vee

Old catalog plugins are unmaintained scripts that run **un-sandboxed with the
user's full privileges**. Porting one is two jobs, in this order:

1. **Audit it.** Decide whether it should run at all.
2. **Convert it.** Modern format, honest trust headers, graceful degradation.

Never skip step 1, and never reorder them. A plugin that fails the audit is
reported, not ported.

## Step 0 — Get the source and the format reference

Source can be a local path, a URL (fetch it), or pasted text. If it is a URL,
record it — it becomes the plugin's provenance note.

Read the Vee format reference before writing any output. Prefer local copies
when the user has the Vee repo checked out (`~/repos/vee/docs/_content/`),
otherwise fetch:

- `https://vee.navbytes.io/guide/plugin-authoring.md` — the format
- `https://vee.navbytes.io/guide/json-output.md` — the JSON format (**target this**)
- `https://vee.navbytes.io/guide/trust-model.md` — `<vee.*>` declarations
- `https://vee.navbytes.io/guide/preferences.md` — typed `<vee.var>` settings
- `https://vee.navbytes.io/llms-full.txt` — everything, one file

`references/audit-checklist.md` in this skill is the audit rubric.
`references/conversion-map.md` is the old-construct → Vee-construct table.

## Step 1 — Security audit (mandatory, before any conversion)

Work through `references/audit-checklist.md` in full. Read **every line** of the
script, including anything after a long blank run at the bottom of the file —
that is where a payload hides. Then classify:

| Verdict | Meaning | What you do |
|---|---|---|
| **BLOCK** | Hostile or indistinguishable from hostile: exfiltrates data, steals credentials, downloads and executes remote code, destroys data, hides its behaviour. | Report the finding with the exact lines. **Do not convert.** Do not produce a "cleaned" version — offer to write a fresh plugin that achieves the stated purpose instead. |
| **WARN** | Legitimate but risky: broad network access, reads secrets, runs `sudo`, writes outside its own cache, uses a deprecated/unpinned dependency. | Report each finding, then convert — with the risk declared honestly in `<vee.*>` headers and, where the behaviour is optional, moved behind an opt-in `<vee.var>` that defaults to off. |
| **CLEAN** | Reads local state, hits a documented API for its stated purpose, no surprises. | Report "no findings", convert. |

**Always report every finding, including on a CLEAN verdict** (say so explicitly)
— the audit output is the deliverable the user judges the plugin by, not a
formality on the way to the code.

**Data leaving the machine is the headline finding.** For every outbound call,
report: the destination host, what is sent, whether the user chose to send it,
and whether the endpoint is the plugin's stated purpose. A weather plugin
calling a weather API is expected; the same plugin POSTing `$USER`, the hostname,
or a machine ID anywhere is an exfiltration finding even when the author calls
it "analytics".

Present the audit **before** the converted plugin, as a short table of
`severity · line · what · why it matters`. Never bury a finding in a comment
inside the generated code and call it disclosed.

## Step 2 — Convert

Target the **JSON output format** (`{"vee":1,…}`) unless the plugin streams
(`<swiftbar.type>streamable</swiftbar.type>`), which stays on the text protocol.
JSON removes the whole class of `|`-quoting and escaping bugs these old scripts
are full of.

Rules for the converted plugin:

1. **Preserve behaviour, not bugs.** Same information, same links, same actions.
   Silently dropping a feature is a worse outcome than porting it with a warning.
2. **Rename all metadata to the `<vee.*>` namespace** (`<xbar.title>` →
   `<vee.title>`, `<swiftbar.type>` → `<vee.type>`). Functionally identical —
   Vee switches on the key, not the namespace — but it is what distinguishes a
   converted plugin from a copied one. It does cost xbar/SwiftBar portability;
   if the user needs that, leave the originals and say so. Environment variables
   are exempt: `SWIFTBAR_PLUGIN_CACHE_PATH`/`DATA_PATH` have no `VEE_*` twin.
3. **Honest `<vee.*>` trust headers**, derived from what the code *does*, not what
   the original author claimed: `<vee.network>` (every host), `<vee.secrets>`,
   `<vee.exec>` (every binary), `<vee.filesystem.read>` / `.write>`.
   Declaring more than it uses is fine; declaring less is a bug.
4. **Hardcoded credentials become `<vee.var>`** named so Vee treats them as
   secrets (`*_TOKEN`, `*_APIKEY`, `*_PASSWORD` → Keychain-stored, masked). Never
   carry a literal key across into the new file, even the original author's own.
5. **Graceful degradation.** Missing dependency, missing token, no network, empty
   result — each gets one clear, actionable row. Never a traceback in the menu bar.
6. **Bound everything.** Add `--max-time` to every `curl`, cap loops and result
   counts, and keep the whole run under ~3 seconds.
7. **Destructive rows get `"searchable": false`** so a typed query plus Return
   can never trigger them.
8. **State goes in a per-plugin directory.** `$SWIFTBAR_PLUGIN_CACHE_PATH` for
   anything you can regenerate; `$SWIFTBAR_PLUGIN_DATA_PATH` when losing it
   breaks something (a PID you spawned, say). `$TMPDIR` is not state — macOS
   prunes it. Every run is a fresh process; old plugins that assumed otherwise
   need this rewritten.
9. **Modernize dead system calls.** Removed or neutered on current macOS:
   `airport -I`, `/usr/bin/python` (2.x), `networksetup` subcommands that now
   require authorization. See `references/conversion-map.md`.
10. **Keep the filename interval** (`name.INTERVAL.ext`) and raise it if the
    original polled a remote API aggressively. A plugin whose display counts
    down or ticks needs an interval at all — without one it renders once and
    goes stale.
11. **Ship it non-executable** and tell the user to read it before `chmod +x`.

## Step 3 — Verify

```sh
vee lint <path>      # must exit 0 with "No lint findings."
vee render <path>    # eyeball the menu tree it builds
```

Iterate until clean. If `vee` is not installed, say so and note that the plugin
is unverified rather than implying it passed.

## Output shape

```
## Audit — <plugin name>
Verdict: CLEAN | WARN | BLOCK
<table of findings, or "No findings.">
Data leaving the machine: <hosts and payloads, or "none">

## Converted plugin
<path written>  — vee lint: <result>
<what changed and why, 3–6 bullets>
<what the user must configure before it works>
```

On **BLOCK**, stop after the audit section and say plainly what you will not do
and what you can do instead.
