# Audit checklist

Work top to bottom. Read every line of the script including anything below a
long run of blank lines — that is where a payload hides. Quote exact line
numbers in findings.

## 1. Exfiltration — data leaving the machine (highest priority)

Enumerate **every** outbound call, then for each one answer: destination host,
payload, did the user choose to send it, is it the plugin's stated purpose.

Grep for: `curl`, `wget`, `nc`, `ncat`, `openssl s_client`, `ssh`, `scp`, `rsync`,
`ftp`, `telnet`, `/dev/tcp/`, `urllib`, `requests`, `httplib`, `http.client`,
`URLSession`, `fetch(`, `axios`, `Net::HTTP`, `LWP`, `socket`.

**BLOCK** when any of these leaves the machine:
- Shell history, `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.netrc`, `.env` files,
  Keychain contents, browser profile data, `login.keychain-db`.
- Clipboard contents, screenshots, keystrokes, audio, camera, location.
- Directory listings of `$HOME`, file contents the plugin did not create.
- Hostname / `$USER` / serial number / MAC / machine UUID sent as telemetry to a
  host that is not the plugin's stated service — this is an exfiltration finding
  regardless of what the author calls it.

**WARN** on: an outbound call to a host unrelated to the stated purpose; any URL
shortener; a hardcoded IP; a domain that looks typosquatted
(`githubb.com`, `raw-githubusercontent.com`, `api.githubb.io`).

Also flag **DNS-channel** exfiltration: `dig`/`nslookup`/`host` against a
subdomain built from local data (`$(whoami).attacker.tld`).

## 2. Remote code execution

**BLOCK** unconditionally:
- `curl … | sh`, `wget -O- … | bash`, `curl … | python3` — download-and-run in
  any spelling, including via a variable.
- `eval` / `exec` / `source` on anything fetched, decoded, or user-influenced.
- `base64 -d`, `xxd -r`, `openssl enc -d`, `gunzip` into an interpreter.
- Long opaque blobs (base64, hex, `\x` escapes, rot13) — decode them and audit
  the result. An obfuscated payload is BLOCK even if the decode looks benign,
  because obfuscation in a menu-bar script has no legitimate use.
- `osascript` running a string assembled at runtime.
- Writing to `~/Library/LaunchAgents`, `~/Library/LaunchDaemons`, `crontab`,
  login items, `~/.zshrc`/`~/.bash_profile`, or any other persistence mechanism.

## 3. Credentials and secrets

- Hardcoded API keys, tokens, passwords, private keys → **WARN**, and they must
  be replaced by `<xbar.var>` on conversion, never carried across.
- Reads of `~/.ssh/id_*`, `~/.aws/credentials`, `~/.netrc`, `~/.docker/config.json`,
  `~/.kube/config`, `.env`, `*.pem`, `*.p12` → **WARN** if used locally for the
  plugin's stated purpose, **BLOCK** if any of it is transmitted.
- `security find-generic-password`/`find-internet-password` → **WARN** (Keychain
  read); **BLOCK** if the value is transmitted or logged.
- A token that is echoed into the menu, a log, or a temp file → **WARN**, fix on
  conversion.

## 4. Destructive and privileged operations

- `rm -rf` with a variable in the path, `rm -rf /`, `rm -rf ~`, `> /dev/sda`,
  `diskutil erase*`, `dd of=/dev/*`, `mkfs`, `killall -9` unscoped → **BLOCK**.
- `sudo`, `osascript -e 'do shell script … with administrator privileges'`,
  `security unlock-keychain`, `chmod 777`, `chown` outside the plugin's own
  files, `csrutil`, `spctl --master-disable`, `defaults write` on a system
  domain → **WARN** at minimum; **BLOCK** when combined with anything fetched.
- Anything that fires **on load** rather than on an explicit click. A menu render
  happens automatically every interval; a destructive action must be click-gated
  and, in Vee, `"searchable": false`.

## 5. Injection and quoting

- Unquoted `$VAR` inside a command built by string concatenation, where the
  value comes from an API response, filename, git branch, or container name.
- `eval "$something"`, backticks around interpolated data.
- API data interpolated into the menu without escaping — in the text protocol an
  unescaped `|` truncates the row; a `\n` splits it. Converting to the JSON
  format fixes this class outright.
- Filenames handled without `--` or `-print0`/`read -r`.

## 6. Supply chain and dependencies

- `pip install`, `npm install`, `gem install`, `brew install` at **runtime** →
  **BLOCK** (a menu refresh must never mutate the machine's toolchain).
- Unpinned dependency fetched from a URL each run → **WARN**.
- `/usr/bin/python` (Python 2, removed from macOS) → broken, fix on conversion.
- A tool the script assumes exists with no guard → fix on conversion.

## 7. Behavioural smells

- Comments that do not match the code; a stated purpose the code exceeds.
- Networking in a plugin whose job is purely local (a clock, a disk gauge).
- Sleeps or retries that keep the process alive past its run.
- Writes anywhere other than `$SWIFTBAR_PLUGIN_CACHE_PATH` /
  `$SWIFTBAR_PLUGIN_DATA_PATH` / an explicitly user-configured path.
- Unbounded loops, unbounded `find` over `$HOME`, or anything that can hang past
  Vee's execution timeout.
- Reading `$XBAR*`/`$SWIFTBAR*` env vars for anything other than their documented
  purpose.

## Severity mapping

- **BLOCK** — any §2 item; any §1 BLOCK item; any §4 destructive item.
- **WARN** — everything else flagged above.
- **CLEAN** — nothing flagged. Say so explicitly; do not stay silent.
