#!/usr/bin/env python3
# <vee.title>Caffeine</vee.title>
# <vee.version>1.1</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Keep your Mac awake — indefinitely or for a timed session — from the menu bar.</vee.desc>
# <vee.dependencies>python3,caffeinate</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/caffeine.png</vee.image>
#
# <vee.exec>caffeinate,ps,pgrep,python3</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_DATA_PATH (falls back to $SWIFTBAR_PLUGIN_CACHE_PATH, then $TMPDIR): vee-caffeine.state, vee-caffeine.log, vee-caffeine.state.lock/</vee.filesystem.write>
#
# A menu-bar front-end for macOS `caffeinate`. Clicking a menu item re-invokes
# this script (via python3) with an action argument; the render pass (no
# args) draws the menu. 1m interval so the "time left" title actually counts
# down (humanize()'s granularity bottoms out at minutes, so 1m matches it
# exactly).
#
# State lives in a stable per-plugin directory, not $TMPDIR: macOS reaps
# $TMPDIR periodically and it differs per login session, which used to orphan
# a running `caffeinate` the moment its state file vanished (it kept running,
# forgotten, with no way to stop it from this menu).

import os
import re
import signal
import subprocess
import sys
from datetime import datetime


# --- Minimal text-protocol builder (no SDK) ----------------------------------
# Vee's xbar/SwiftBar-compatible text format: `text | key=value key2=value2`.
# Escaping/quoting rules mirror https://vee.navbytes.io/guide/plugin-authoring/ — a literal
# `|`/`\` in display text is escaped, and a param value containing whitespace,
# `|`, or `\` is quoted.
_QUOTE_FORCING = frozenset(
    "\t\n\v\f\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff|\\"
) | frozenset(chr(c) for c in range(0x2000, 0x200B))


def _escape_text(value):
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n")


def _quote(value):
    escaped = _escape_text(value)
    if any(ch in _QUOTE_FORCING for ch in value) or value[:1] in ('"', "'"):
        return '"' + escaped.replace('"', '\\"') + '"'
    return escaped


def _fmt(value):
    return "true" if value is True else "false" if value is False else str(value)


def _encode(opts):
    parts = []

    def push(key, value):
        if value is not None:
            parts.append(f"{key}={_quote(_fmt(value))}")

    push("color", opts.get("color"))
    push("size", opts.get("size"))
    if opts.get("shell") is not None:
        push("shell", opts["shell"])
        for i, p in enumerate(opts.get("params") or []):
            push(f"param{i + 1}", p)
    push("refresh", opts.get("refresh"))
    push("disabled", opts.get("disabled"))
    push("checked", opts.get("checked"))
    push("sfimage", opts.get("sfimage"))
    push("sfcolor", opts.get("sf_color"))
    push("searchable", opts.get("searchable"))
    slider = opts.get("slider")
    if slider is not None:
        push("slider", f"{_fmt(slider['min'])},{_fmt(slider['max'])},{_fmt(slider['value'])}")
    return " | " + " ".join(parts) if parts else ""


class Section:
    def __init__(self, lines, depth=0):
        self._lines = lines
        self._depth = depth

    def item(self, text, **opts):
        self._lines.append("-" * (self._depth * 2) + _escape_text(text) + _encode(opts))
        return self

    def separator(self):
        self._lines.append("-" * (self._depth * 2) + "---")
        return self

    def submenu(self, text, **opts):
        self.item(text, **opts)
        return Section(self._lines, self._depth + 1)


class Menu:
    def __init__(self):
        self._titles = []
        self._body = []

    def title(self, text, **opts):
        self._titles.append(_escape_text(text) + _encode(opts))
        return self

    @property
    def dropdown(self):
        return Section(self._body)

    def to_string(self):
        head = "\n".join(self._titles)
        if self._body:
            return f"{head}\n---\n" + "\n".join(self._body)
        return head

    def print(self):
        sys.stdout.write(self.to_string() + "\n")


PLUGIN = os.environ.get("VEE_PLUGIN_PATH") or os.environ.get("SWIFTBAR_PLUGIN_PATH") or sys.argv[0]
BASE_DIR = (
    os.environ.get("SWIFTBAR_PLUGIN_DATA_PATH")
    or os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH")
    or os.environ.get("TMPDIR")
    or "/tmp"
).rstrip("/")
os.makedirs(BASE_DIR, exist_ok=True)

STATE = os.path.join(BASE_DIR, "vee-caffeine.state")  # "PID ENDTIME DUR START MODE" (ENDTIME/DUR 0 = indefinite)
LOG = os.path.join(BASE_DIR, "vee-caffeine.log")
LOCKD = f"{STATE}.lock"
LOCK_STALE_SECS = 15  # comfortably above any legitimate critical section (a spawn + a write)
LOG_MAX_BYTES = 131072  # 128 KiB — ponytail: size-triggered trim, not a rotating-log lib

AMBER = "#F5A623"
DIM = "#8A8F98"

_UINT_RE = re.compile(r"[0-9]+")


def now():
    return int(datetime.now().timestamp())


def is_uint(s):
    return bool(s) and _UINT_RE.fullmatch(s) is not None


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
        if os.path.getsize(LOG) > LOG_MAX_BYTES:
            with open(LOG, "rb") as f:
                f.seek(-LOG_MAX_BYTES, os.SEEK_END)
                tail = f.read()
            tmp = f"{LOG}.tmp"
            with open(tmp, "wb") as f:
                f.write(tail)
            os.replace(tmp, LOG)
    except OSError:
        pass


# ── Lock (atomic via mkdir, owner-checked, self-healing) ─────────────────────
# A bare mkdir-lock with no owner wedges forever the moment its holder is
# SIGKILLed (Vee's own execution-timeout behavior) instead of releasing on
# exit. Recording the owner PID lets us tell "held" from "abandoned" and
# break the latter automatically instead of requiring someone to know to
# rmdir a path in a cache dir by hand.

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours — still alive
    except OSError:
        return False
    return True


def acquire():
    try:
        os.mkdir(LOCKD)
    except FileExistsError:
        owner_s = None
        try:
            with open(os.path.join(LOCKD, "pid")) as f:
                owner_s = f.read().strip()
        except OSError:
            pass
        if owner_s and is_uint(owner_s) and _pid_alive(int(owner_s)):
            try:
                age = now() - int(os.stat(LOCKD).st_mtime)
            except OSError:
                age = 0
            if age < LOCK_STALE_SECS:
                return False  # genuinely held
        # Stale: owner missing, owner's process is gone, or held far longer
        # than any action here should take. Break it and take over.
        # ponytail: mkdir-then-pid-write has a microsecond race window
        # between two concurrent acquirers; not worth cross-process locking
        # for a menu click.
        log(f"acquire: breaking stale lock (owner={owner_s or 'none'})")
        try:
            os.remove(os.path.join(LOCKD, "pid"))
        except OSError:
            pass
        try:
            os.rmdir(LOCKD)
        except OSError:
            pass
        try:
            os.mkdir(LOCKD)
        except OSError:
            return False
    try:
        with open(os.path.join(LOCKD, "pid"), "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    return True


def release():
    try:
        os.remove(os.path.join(LOCKD, "pid"))
    except OSError:
        pass
    try:
        os.rmdir(LOCKD)
    except OSError:
        pass


# ── Process helpers ───────────────────────────────────────────────────────────

def proc_start_epoch(pid):
    """Real start time of `pid`, as a unix epoch — used to tell "the
    caffeinate we started" apart from an unrelated process that later
    recycled the same PID. LC_ALL=C is forced because ps's lstart= wording
    (day/month order) is locale-dependent and would otherwise break the
    parse on non-C locales."""
    env = dict(os.environ, LC_ALL="C")
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = re.sub(r"\s+", " ", out.stdout.strip())
    if not raw:
        return None
    try:
        return int(datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").timestamp())
    except ValueError:
        return None


def caffeinate_matches(pid_s, want_s):
    """True iff `pid_s` is still a `caffeinate` process that started at
    exactly `want_s` (name alone isn't enough: a recycled PID could belong
    to a different caffeinate invocation entirely)."""
    if not is_uint(pid_s or ""):
        return False
    pid = int(pid_s)
    try:
        comm = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    if re.sub(r"\s+", "", comm) != "caffeinate":
        return False
    if not is_uint(want_s or ""):
        return False
    return proc_start_epoch(pid) == int(want_s)


def list_strays(mine_pid):
    """Every `caffeinate` PID on the machine other than `mine_pid`, as
    (pid, start, cmd) tuples — this plugin never assumes a stray belongs to
    it; it shows the full command so the user can tell theirs apart from
    some other tool's assertion."""
    strays = []
    try:
        out = subprocess.run(["pgrep", "-x", "caffeinate"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return strays
    for line in out.stdout.splitlines():
        line = line.strip()
        if not is_uint(line):
            continue
        pid = int(line)
        if pid == mine_pid:
            continue
        start = proc_start_epoch(pid)
        if start is None:
            continue
        try:
            cmd = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=5,
            ).stdout.replace("\n", "")
        except (OSError, subprocess.SubprocessError):
            cmd = ""
        strays.append((pid, start, cmd))
    return strays


# ── State helpers ─────────────────────────────────────────────────────────────

def read_state():
    """Returns (active, pid, endtime, dur, start, mode) — a pure query, no
    side effects. `pid`/`start` stay populated even when inactive (matching
    the bash original, which leaves PID/START set after a failed match) so
    `list_strays` can still exclude a session that just expired."""
    pid = endtime = dur = start = 0
    mode = "dimsu"
    try:
        with open(STATE) as f:
            line = f.readline()
    except OSError:
        return False, pid, endtime, dur, start, mode
    parts = line.split()
    if len(parts) < 5:
        return False, pid, endtime, dur, start, mode
    pid_s, endtime_s, dur_s, start_s, mode = parts[0], parts[1], parts[2], parts[3], parts[4]
    pid = int(pid_s) if is_uint(pid_s) else 0
    start = int(start_s) if is_uint(start_s) else 0
    if not caffeinate_matches(pid_s, start_s):
        return False, pid, endtime, dur, start, mode
    endtime = int(endtime_s) if is_uint(endtime_s) else 0
    dur = int(dur_s) if is_uint(dur_s) else 0
    if endtime > 0 and now() >= endtime:
        return False, pid, endtime, dur, start, mode
    return True, pid, endtime, dur, start, mode


def clear_state():
    try:
        with open(STATE) as f:
            line = f.readline()
    except OSError:
        line = ""
    parts = line.split()
    if len(parts) >= 5:
        p, s = parts[0], parts[3]
        if caffeinate_matches(p, s):
            try:
                os.kill(int(p), signal.SIGTERM)
            except OSError:
                pass
            log(f"clear_state killed caffeinate PID={p}")
        else:
            log(f"clear_state: PID={p or '?'} not a matching caffeinate, skip kill")
    try:
        os.remove(STATE)
    except OSError:
        pass


def start_session(secs, mode):
    if not acquire():
        log("start_session: lock held, exit")
        return
    try:
        clear_state()
        flags = ["-is"] if mode == "is" else ["-dimsu"]
        cmd = ["caffeinate", *flags]
        if secs > 0:
            cmd += ["-t", str(secs)]
        with open(os.devnull, "wb") as devnull:
            proc = subprocess.Popen(cmd, stdout=devnull, stderr=devnull, start_new_session=True)
        pid = proc.pid
        start = proc_start_epoch(pid) or now()
        endtime = now() + secs if secs > 0 else 0
        dur = secs if secs > 0 else 0
        with open(STATE, "w") as f:
            f.write(f"{pid} {endtime} {dur} {start} {mode}\n")
        log(f"start_session: secs={secs} mode={mode} PID={pid}")
    finally:
        release()


# ── Human-readable duration ───────────────────────────────────────────────────

def humanize(s):  # seconds -> "1h 23m" / "45m" / "30s"
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{s}s"


# ── Actions (invoked from clicked items) ──────────────────────────────────────

def run_action(argv):
    action = argv[0] if argv else ""
    arg = lambda i: argv[i] if len(argv) > i else ""

    if action == "start":
        arg2 = arg(1)
        if not is_uint(arg2):
            log(f"start: invalid arg={arg2}")
            return
        mode = arg(2) or "dimsu"
        if mode not in ("dimsu", "is"):
            mode = "dimsu"
        start_session(int(arg2), mode)
        return

    if action == "startmin":
        local_min = arg(1) or os.environ.get("VEE_CONTROL_VALUE", "0")
        if not is_uint(local_min):
            log(f"startmin: invalid arg={local_min}")
            return
        local_min = int(local_min)
        if local_min <= 0:
            log("startmin: 0 rejected")
            return
        start_session(local_min * 60, "dimsu")
        return

    if action == "stop":
        if not acquire():
            log("stop: lock held, exit")
            return
        try:
            log("stop")
            clear_state()
        finally:
            release()
        return

    if action == "adopt":  # argv[1]=pid argv[2]=start-epoch
        pid_s, start_s = arg(1), arg(2)
        if not (is_uint(pid_s) and is_uint(start_s)):
            log("adopt: invalid args")
            return
        if not caffeinate_matches(pid_s, start_s):
            log(f"adopt: PID={pid_s} no longer matches, skip")
            return
        if not acquire():
            log("adopt: lock held, exit")
            return
        try:
            clear_state()
            with open(STATE, "w") as f:
                f.write(f"{pid_s} 0 0 {start_s} unknown\n")
            log(f"adopt: PID={pid_s} now tracked")
        finally:
            release()
        return

    if action == "killstray":  # argv[1]=pid argv[2]=start-epoch, verified by PID+start time
        pid_s, start_s = arg(1), arg(2)
        if not (is_uint(pid_s) and is_uint(start_s)):
            log("killstray: invalid args")
            return
        if caffeinate_matches(pid_s, start_s):
            try:
                os.kill(int(pid_s), signal.SIGTERM)
            except OSError:
                pass
            log(f"killstray: killed PID={pid_s}")
        else:
            log(f"killstray: PID={pid_s} no longer matches, skip")
        return

    # unknown/no action — falls through to render, same as the bash original


# ── Render ────────────────────────────────────────────────────────────────────

PRESETS = [("15 minutes", 900), ("30 minutes", 1800), ("1 hour", 3600), ("2 hours", 7200), ("4 hours", 14400)]


def render():
    active, pid, endtime, dur, start, mode = read_state()
    remain = ""
    if active and endtime > 0:
        remain = humanize(max(0, endtime - now()))

    menu = Menu()
    if active:
        menu.title(remain, sfimage="cup.and.saucer.fill", sf_color=AMBER)
    else:
        # Empty text: `Menu.to_string()` already inserts the `---` (and the
        # single leading space before it) between the title and the
        # dropdown — matching the bash original's literal " | ..." line,
        # which is the same "no text, just the separator" render.
        menu.title("", sfimage="cup.and.saucer", sf_color=DIM)

    d = menu.dropdown

    if active:
        mode_txt = "system awake, display may sleep" if mode == "is" else "display+system awake"
        if remain:
            d.item(f"Awake ({mode_txt}) — {remain} left",
                   sfimage="cup.and.saucer.fill", sf_color=AMBER, disabled=True)
            sleeps_at = datetime.fromtimestamp(endtime).strftime("%-I:%M %p")
            d.item(f"Sleeps at {sleeps_at}", size=11, color=DIM, disabled=True)
        else:
            d.item(f"Awake ({mode_txt}) — until you stop",
                   sfimage="infinity", sf_color=AMBER, disabled=True)
        d.separator()
        d.item("Let it sleep now", sfimage="moon.zzz.fill", sf_color=DIM,
               shell=sys.executable, params=[PLUGIN, "stop"], refresh=True)
    else:
        d.item("Sleep allowed", sfimage="moon.zzz", sf_color=DIM, disabled=True)

    d.separator()
    checked_dimsu = active and endtime == 0 and mode != "is"
    checked_is = active and endtime == 0 and mode == "is"
    d.item("Keep display + system awake — until I stop",
           sfimage="cup.and.saucer.fill", sf_color=AMBER,
           shell=sys.executable, params=[PLUGIN, "start", "0", "dimsu"], refresh=True,
           checked=True if checked_dimsu else None)
    d.item("Keep system awake, allow display sleep — until I stop",
           sfimage="moon.stars", sf_color=AMBER,
           shell=sys.executable, params=[PLUGIN, "start", "0", "is"], refresh=True,
           checked=True if checked_is else None)

    d.separator()
    d.item("Timed session", size=11, color=DIM, disabled=True)
    for label, secs in PRESETS:
        checked = active and dur == secs
        d.item(label, sfimage="timer", shell=sys.executable, params=[PLUGIN, "start", str(secs)],
               refresh=True, checked=True if checked else None)

    seedmin = 60
    if active and dur > 0:
        seedmin = max(5, min(240, dur // 60))
    d.item("Custom duration…", sfimage="slider.horizontal.3",
           slider={"min": 5, "max": 240, "value": seedmin},
           shell=sys.executable, params=[PLUGIN, "startmin"], refresh=True)

    # Strays: every caffeinate on the machine this plugin isn't tracking,
    # shown with its start time and full command so the user — not a
    # blanket pkill — decides what to stop. Also how an orphaned session
    # (state file gone, process still running) gets back under this menu's
    # control. Deliberately no kill-all option.
    strays = list_strays(pid if pid else None)
    if strays:
        d.separator()
        d.item("Other caffeinate processes (not tracked here)", size=11, color=DIM, disabled=True)
        for spid, sstart, scmd in strays:
            started = datetime.fromtimestamp(sstart).strftime("%-I:%M %p")
            row = d.submenu(f"PID {spid} · started {started} · {scmd}",
                             sfimage="exclamationmark.triangle", sf_color=AMBER)
            # The leading space matches the original bash's literal "-- Take
            # control..."/"-- Stop..." — Vee's `--`-prefix depth marker drops
            # exactly `depth*2` dash characters and treats the rest (space
            # included) as the item text.
            row.item(" Take control (track and manage here)", sfimage="hand.raised",
                     shell=sys.executable, params=[PLUGIN, "adopt", str(spid), str(sstart)], refresh=True)
            row.item(" Stop this session", sfimage="xmark.octagon",
                     shell=sys.executable, params=[PLUGIN, "killstray", str(spid), str(sstart)],
                     refresh=True, searchable=False)

    menu.print()


def main():
    argv = sys.argv[1:]
    if argv:
        run_action(argv)
        if argv[0] in ("start", "startmin", "stop", "adopt", "killstray"):
            return
    render()


if __name__ == "__main__":
    main()
