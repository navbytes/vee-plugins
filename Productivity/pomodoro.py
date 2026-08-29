#!/usr/bin/env python3
#
# pomodoro.py — a focus timer that lives in the menu bar.
#
# This is Vee's STREAMING plugin showcase: no filename interval, marked
# <vee.type>streamable</vee.type>, and it stays running forever,
# pushing a full menu render between `~~~` separators once a second instead
# of being re-run on a timer. It uses the file-local text-protocol `Menu`
# builder below (not JSON) because a streaming loop reads far more clearly
# as plain renders, and it escapes the one literal `|` it prints as `\|`.
#
# All state (phase, when the phase ends, today's tally) lives in one file
# under $SWIFTBAR_PLUGIN_CACHE_PATH — every run is normally a fresh process,
# but this one *is* the process, so the file exists mainly so the control
# rows (each a `shell=` re-invocation of this very script with a subcommand)
# can hand instructions to the loop that's already running: a click mutates
# the file and exits immediately; the loop just reads it back on its next
# tick. What it touches: its own cache file (read + write) and `open` to
# fire a macOS notification via `vee://notify` when a phase ends.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <vee.title>Pomodoro</vee.title>
# <vee.version>1.0</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>A streaming menu-bar focus timer — focus/break phases, a daily tally, and one-click controls.</vee.desc>
# <vee.dependencies>python3</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
# <vee.image>https://raw.githubusercontent.com/navbytes/vee-plugins/main/docs/screenshots/pomodoro.png</vee.image>
#
# <vee.var>number(FOCUS_MINUTES=25): Length of a focus session, in minutes.</vee.var>
# <vee.var>number(BREAK_MINUTES=5): Length of a short break, in minutes.</vee.var>
# <vee.var>number(LONG_BREAK_MINUTES=15): Length of the long break every 4th completed focus session.</vee.var>
# <vee.var>boolean(NOTIFY=true): Post a notification when a phase ends.</vee.var>
#
# <vee.type>streamable</vee.type>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>notifications</vee.capabilities>
# <vee.exec>open,python3</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/pomodoro-state</vee.filesystem.write>

import os
import re
import subprocess
import sys
import time
from urllib.parse import quote


# --- Minimal text-protocol builder ------------------------------------------
# Vee's xbar/SwiftBar-compatible text format: `text | key=value key2=value2`.
# Escaping/quoting rules mirror https://vee.navbytes.io/guide/plugin-authoring/
# — a literal `|`/`\` in display text is escaped, and a param value
# containing whitespace, `|`, or `\` is quoted.
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
    if opts.get("shell") is not None:
        push("shell", opts["shell"])
        for i, p in enumerate(opts.get("params") or []):
            push(f"param{i + 1}", p)
    push("sfimage", opts.get("sfimage"))
    push("sfcolor", opts.get("sf_color"))
    push("searchable", opts.get("searchable"))
    progress = opts.get("progress")
    if progress is not None:
        push("progress", f"{_fmt(progress['value'])},{_fmt(progress['max'])}")
    chart = opts.get("chart")
    if chart is not None:
        push(chart["kind"], ",".join(_fmt(v) for v in chart["values"]))
        push("chartlabels", ",".join(chart["labels"]))
        push("chartcolors", ",".join(chart["colors"]))
    push("accessoryw", opts.get("accessory_w"))
    push("accessoryh", opts.get("accessory_h"))
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


# ---------------------------------------------------------------------------
# Setup: preferences (sanitized — a bad value falls back to its default
# rather than producing a negative/zero-length timer), cache path, and this
# script's own absolute path for the control rows' `shell=` target.
# ---------------------------------------------------------------------------

_MINUTES_RE = re.compile(r"[0-9]+")


def sanitize_minutes(value, default):
    """A positive integer or the default — mirrors the bash `[[ =~ ^[0-9]+$ ]]`."""
    if value is not None and _MINUTES_RE.fullmatch(value) and int(value) > 0:
        return int(value)
    return default


FOCUS_MINUTES = sanitize_minutes(os.environ.get("FOCUS_MINUTES", "25"), 25)
BREAK_MINUTES = sanitize_minutes(os.environ.get("BREAK_MINUTES", "5"), 5)
LONG_BREAK_MINUTES = sanitize_minutes(os.environ.get("LONG_BREAK_MINUTES", "15"), 15)
NOTIFY = os.environ.get("NOTIFY", "true") in ("true", "1")

SCRIPT_PATH = os.environ.get("VEE_PLUGIN_PATH") or sys.argv[0]

CACHE_DIR = os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH") or os.environ.get("TMPDIR") or "/tmp"
os.makedirs(CACHE_DIR, exist_ok=True)
STATE_FILE = os.path.join(CACHE_DIR, "pomodoro-state")

# ---------------------------------------------------------------------------
# State: phase (idle|focus|break), the epoch second the current phase
# started/ends, a one-shot notify guard, today's completed-pomodoro count,
# today's date, and today's accumulated focus/break seconds (for the
# stackedbar). Plain `key=value` lines, read field-by-field into a fixed
# whitelist of fields — never sourced/eval'd, so a corrupt or hostile file
# can set known fields to garbage but can never run a command.
# ---------------------------------------------------------------------------

STATE_FIELDS = (
    "phase", "started_at", "ends_at", "notified_for",
    "count", "day", "focus_sec", "break_sec",
)
_INT_FIELDS = ("started_at", "ends_at", "notified_for", "count", "focus_sec", "break_sec")
_IS_INT_RE = re.compile(r"-?[0-9]+")
_IS_DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def reset_defaults():
    return {
        "phase": "idle", "started_at": 0, "ends_at": 0, "notified_for": 0,
        "count": 0, "day": "", "focus_sec": 0, "break_sec": 0,
    }


def load_state():
    state = reset_defaults()
    try:
        with open(STATE_FILE) as f:
            for line in f:
                k, sep, v = line.rstrip("\n").partition("=")
                if sep and k in state:
                    state[k] = v
    except OSError:
        pass
    # Missing file, truncated write, or garbage field → a safe idle default,
    # never a crash on the arithmetic below.
    if state["phase"] not in ("focus", "break", "idle"):
        state["phase"] = "idle"
    for field in _INT_FIELDS:
        v = str(state[field])
        state[field] = int(v) if _IS_INT_RE.fullmatch(v) else 0
    if not _IS_DAY_RE.fullmatch(state["day"] or ""):
        state["day"] = ""
    return state


def save_state(state):
    # Write-then-rename so a reader (the loop's own next tick, or a control
    # row's fresh process) never sees a half-written file.
    tmp = f"{STATE_FILE}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            for field in STATE_FIELDS:
                f.write(f"{field}={state[field]}\n")
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def check_day_rollover(state, today):
    """A new local day discards yesterday's tally. ponytail: a session left
    running across midnight just gets cut to idle rather than split across
    two days — good enough for a menu-bar timer, revisit if someone actually
    works through midnight."""
    if state["day"] != today:
        state.update(phase="idle", started_at=0, ends_at=0, notified_for=0,
                      count=0, focus_sec=0, break_sec=0, day=today)


def bank_elapsed(state, now):
    """Adds the seconds actually spent in the current phase (capped at its
    own end, so a natural completion banks the full length and an early
    pause/skip banks only what elapsed) into today's focus/break totals."""
    if state["phase"] == "idle":
        return
    cap = min(state["ends_at"], now)
    elapsed = max(0, cap - state["started_at"])
    if state["phase"] == "focus":
        state["focus_sec"] += elapsed
    else:
        state["break_sec"] += elapsed


def break_minutes_for_count(count):
    """Every 4th completed pomodoro earns the long break; classic Pomodoro
    Technique cadence."""
    return LONG_BREAK_MINUTES if count > 0 and count % 4 == 0 else BREAK_MINUTES


# ---------------------------------------------------------------------------
# Subcommand mode — a control row's `shell=<python3> param1=<this script>
# param2=<subcommand>` lands here. Mutate the state file and exit; never
# enter the streaming loop.
# ---------------------------------------------------------------------------

def run_subcommand(cmd):
    now = int(time.time())
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    state = load_state()
    check_day_rollover(state, today)

    if cmd == "start":
        bank_elapsed(state, now)
        state.update(phase="focus", started_at=now, ends_at=now + FOCUS_MINUTES * 60, notified_for=0)
    elif cmd == "break":
        bank_elapsed(state, now)
        mins = break_minutes_for_count(state["count"])
        state.update(phase="break", started_at=now, ends_at=now + mins * 60, notified_for=0)
    elif cmd == "pause":
        bank_elapsed(state, now)
        state.update(phase="idle", started_at=0, ends_at=0, notified_for=0)
    elif cmd == "reset":
        state.update(phase="idle", started_at=0, ends_at=0, notified_for=0,
                      count=0, focus_sec=0, break_sec=0)
    elif cmd == "skip":
        bank_elapsed(state, now)
        if state["phase"] == "focus":
            mins = break_minutes_for_count(state["count"])
            state.update(phase="break", started_at=now, ends_at=now + mins * 60)
        else:
            state.update(phase="focus", started_at=now, ends_at=now + FOCUS_MINUTES * 60)
        state["notified_for"] = 0
    # else: unknown subcommand — no-op

    save_state(state)


# ---------------------------------------------------------------------------
# Streaming mode — no arguments. Runs forever; Vee restarts it with backoff
# if it ever exits. Every tick is O(1) work (a handful of dict/string ops,
# one `time.time()`, one `Menu` render) so there is nothing here that grows
# unbounded, and the only subprocess (`open`, rarely) is short-lived and
# never forked more than once per second.
# ---------------------------------------------------------------------------

def fmt_mmss(secs):
    if secs < 0:
        secs = 0
    return f"{secs // 60:02d}:{secs % 60:02d}"


def notify(title, body):
    """vee://notify — see https://vee.navbytes.io/guide/cli-and-urls/#the-notify-action.
    `plugin=` makes the alert actionable (Re-run/Silence/Open Log) and
    coalesces repeats instead of stacking."""
    plugin_id = os.environ.get("VEE_PLUGIN_ID", "")
    url = (
        "vee://notify?plugin=" + quote(plugin_id, safe="")
        + "&title=" + quote(title, safe="")
        + "&body=" + quote(body, safe="")
    )
    try:
        subprocess.run(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def render(state, now):
    phase = state["phase"]
    if phase in ("focus", "break"):
        remaining = max(0, state["ends_at"] - now)
        frac_num = max(0, now - state["started_at"])
        frac_den = state["ends_at"] - state["started_at"]
        if frac_den <= 0:
            frac_den = 1
        if phase == "focus":
            color = "red" if remaining <= 60 else "orange"
            sfimg, phase_label = "flame.fill", "Focus"
        else:
            color = "green"
            sfimg, phase_label = "cup.and.saucer.fill", "Break"
    else:
        remaining, color, sfimg, phase_label = 0, "gray", "pause.circle", "Idle"
        frac_num, frac_den = 0, 1

    menu = Menu()
    menu.title(fmt_mmss(remaining), sfimage=sfimg, color=color)
    d = menu.dropdown
    d.item(f"Phase: {phase_label}", color=color, sfimage=sfimg)
    d.item("Phase progress", progress={"value": frac_num, "max": frac_den},
           color=color, accessory_w=160, accessory_h=8)
    d.item(f"Completed today: {state['count']}")

    fmin, bmin = state["focus_sec"] // 60, state["break_sec"] // 60
    if fmin + bmin > 0:
        # A literal `|` in display text is escaped as `\|` by `_escape_text`.
        d.item(f"Today: {fmin}m focus | {bmin}m break",
               chart={"kind": "stackedbar", "values": [fmin, bmin],
                      "labels": ["Focus", "Break"], "colors": ["orange", "green"]},
               accessory_w=180, accessory_h=14)
    else:
        d.item("Today: no time logged yet", color="gray")

    d.separator()
    d.item("Start focus", shell=sys.executable, params=[SCRIPT_PATH, "start"], sfimage="play.fill")
    d.item("Start break", shell=sys.executable, params=[SCRIPT_PATH, "break"],
           sfimage="cup.and.saucer", sf_color="green")
    d.item("Pause", shell=sys.executable, params=[SCRIPT_PATH, "pause"], sfimage="pause.fill")
    d.item("Skip to next phase", shell=sys.executable, params=[SCRIPT_PATH, "skip"], sfimage="forward.fill")
    d.item("Reset", shell=sys.executable, params=[SCRIPT_PATH, "reset"],
           sfimage="arrow.counterclockwise", searchable=False)

    # The `~~~` goes AFTER the rendered block: Vee's StreamAccumulator emits
    # the buffer when it *sees* the separator, so a trailing separator
    # flushes this frame immediately. A leading one shows every frame a tick
    # late and paints an empty menu first. Written and flushed as one call so
    # a partially-buffered frame is never left sitting in a pipe.
    sys.stdout.write(menu.to_string() + "\n~~~\n")
    sys.stdout.flush()


def run_loop():
    while True:
        now = int(time.time())
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        state = load_state()
        check_day_rollover(state, today)

        # Natural phase completion: fires exactly once per (phase, ends_at)
        # pair — notified_for guards every later tick at the same ends_at
        # from re-firing.
        if state["phase"] != "idle" and now >= state["ends_at"] and state["notified_for"] != state["ends_at"]:
            bank_elapsed(state, now)
            if state["phase"] == "focus":
                state["count"] += 1
                title, body = "Focus session complete", "Nice work — take a break."
            else:
                title, body = "Break's over", "Ready for another focus session?"
            state["notified_for"] = state["ends_at"]
            save_state(state)
            if NOTIFY:
                notify(title, body)

        render(state, now)
        time.sleep(1)


def main():
    if len(sys.argv) > 1:
        run_subcommand(sys.argv[1])
        return
    try:
        run_loop()
    except BrokenPipeError:
        # Vee closed its end of the pipe (quit, plugin reload). bash's
        # `echo` dies silently on the same SIGPIPE; exit the same way
        # instead of spewing a traceback on the next tick's flush.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


if __name__ == "__main__":
    main()
