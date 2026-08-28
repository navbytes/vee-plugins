#!/usr/bin/env bash
#
# pomodoro.sh — a focus timer that lives in the menu bar.
#
# This is Vee's STREAMING plugin showcase: no filename interval, marked
# <vee.type>streamable</vee.type>, and it stays running forever,
# pushing a full menu render between `~~~` separators once a second instead
# of being re-run on a timer. It uses the text protocol (not JSON) because a
# streaming loop reads far more clearly as plain `echo` lines, and takes
# care to escape the one literal `|` it prints as `\|`.
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
# <vee.dependencies>bash</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
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
# <vee.exec>open,bash</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/pomodoro-state</vee.filesystem.write>

set -u

# ---------------------------------------------------------------------------
# Setup: preferences (sanitized — a bad value falls back to its default
# rather than producing a negative/zero-length timer), cache path, and this
# script's own absolute path for the control rows' `shell=` target.
# ---------------------------------------------------------------------------

sanitize_minutes() {
  # $1 = candidate value, $2 = default. A positive integer or the default.
  if [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -gt 0 ]; then printf '%s' "$1"; else printf '%s' "$2"; fi
}
FOCUS_MINUTES=$(sanitize_minutes "${FOCUS_MINUTES:-25}" 25)
BREAK_MINUTES=$(sanitize_minutes "${BREAK_MINUTES:-5}" 5)
LONG_BREAK_MINUTES=$(sanitize_minutes "${LONG_BREAK_MINUTES:-15}" 15)
case "${NOTIFY:-true}" in
  true | 1) NOTIFY="true" ;;
  *) NOTIFY="false" ;;
esac

SCRIPT_PATH="${VEE_PLUGIN_PATH:-$0}"

CACHE_DIR="${SWIFTBAR_PLUGIN_CACHE_PATH:-${TMPDIR:-/tmp}}"
mkdir -p "$CACHE_DIR" 2>/dev/null || true
STATE_FILE="$CACHE_DIR/pomodoro-state"

# ---------------------------------------------------------------------------
# State: phase (idle|focus|break), the epoch second the current phase
# started/ends, a one-shot notify guard, today's completed-pomodoro count,
# today's date, and today's accumulated focus/break seconds (for the
# stackedbar). Plain `key=value` lines, read field-by-field into a fixed
# whitelist of globals — never sourced/eval'd, so a corrupt or hostile file
# can set known fields to garbage but can never run a command.
# ---------------------------------------------------------------------------

is_int() { [[ "$1" =~ ^-?[0-9]+$ ]]; }

reset_defaults() {
  PHASE="idle"; STARTED_AT=0; ENDS_AT=0; NOTIFIED_FOR=0
  COUNT=0; DAY=""; FOCUS_SEC=0; BREAK_SEC=0
}

load_state() {
  reset_defaults
  if [ -f "$STATE_FILE" ]; then
    local k v
    while IFS='=' read -r k v; do
      case "$k" in
        phase) PHASE="$v" ;;
        started_at) STARTED_AT="$v" ;;
        ends_at) ENDS_AT="$v" ;;
        notified_for) NOTIFIED_FOR="$v" ;;
        count) COUNT="$v" ;;
        day) DAY="$v" ;;
        focus_sec) FOCUS_SEC="$v" ;;
        break_sec) BREAK_SEC="$v" ;;
      esac
    done < "$STATE_FILE" 2>/dev/null
  fi
  # Missing file, truncated write, or garbage field → a safe idle default,
  # never a crash on `((...))` arithmetic below.
  case "$PHASE" in focus | break | idle) ;; *) PHASE="idle" ;; esac
  is_int "$STARTED_AT" || STARTED_AT=0
  is_int "$ENDS_AT" || ENDS_AT=0
  is_int "$NOTIFIED_FOR" || NOTIFIED_FOR=0
  is_int "$COUNT" || COUNT=0
  is_int "$FOCUS_SEC" || FOCUS_SEC=0
  is_int "$BREAK_SEC" || BREAK_SEC=0
  [[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || DAY=""
}

save_state() {
  # Write-then-rename so a reader (the loop's own next tick, or a control
  # row's fresh process) never sees a half-written file.
  local tmp="$STATE_FILE.tmp.$$"
  {
    printf 'phase=%s\n' "$PHASE"
    printf 'started_at=%s\n' "$STARTED_AT"
    printf 'ends_at=%s\n' "$ENDS_AT"
    printf 'notified_for=%s\n' "$NOTIFIED_FOR"
    printf 'count=%s\n' "$COUNT"
    printf 'day=%s\n' "$DAY"
    printf 'focus_sec=%s\n' "$FOCUS_SEC"
    printf 'break_sec=%s\n' "$BREAK_SEC"
  } > "$tmp" 2>/dev/null && mv -f "$tmp" "$STATE_FILE" 2>/dev/null
}

# A new local day discards yesterday's tally. ponytail: a session left
# running across midnight just gets cut to idle rather than split across two
# days — good enough for a menu-bar timer, revisit if someone actually works
# through midnight.
check_day_rollover() {
  local today="$1"
  if [ "$DAY" != "$today" ]; then
    PHASE="idle"; STARTED_AT=0; ENDS_AT=0; NOTIFIED_FOR=0
    COUNT=0; FOCUS_SEC=0; BREAK_SEC=0; DAY="$today"
  fi
}

# Add the seconds actually spent in the current phase (capped at its own
# end, so a natural completion banks the full length and an early
# pause/skip banks only what elapsed) into today's focus/break totals.
bank_elapsed() {
  local now="$1" cap elapsed
  [ "$PHASE" = "idle" ] && return
  cap=$ENDS_AT
  [ "$now" -lt "$cap" ] && cap=$now
  elapsed=$((cap - STARTED_AT))
  [ "$elapsed" -lt 0 ] && elapsed=0
  if [ "$PHASE" = "focus" ]; then
    FOCUS_SEC=$((FOCUS_SEC + elapsed))
  else
    BREAK_SEC=$((BREAK_SEC + elapsed))
  fi
}

# Every 4th completed pomodoro earns the long break; classic Pomodoro Technique cadence.
break_minutes_for_count() {
  if [ "$COUNT" -gt 0 ] && [ $((COUNT % 4)) -eq 0 ]; then
    printf '%s' "$LONG_BREAK_MINUTES"
  else
    printf '%s' "$BREAK_MINUTES"
  fi
}

# ---------------------------------------------------------------------------
# Subcommand mode — a control row's `shell=/bin/bash param0=<this script>
# param1=<subcommand>` lands here. Mutate the state file and exit; never
# enter the streaming loop.
# ---------------------------------------------------------------------------

if [ $# -gt 0 ]; then
  read -r NOW TODAY < <(date +"%s %F")
  load_state
  check_day_rollover "$TODAY"

  case "$1" in
    start)
      bank_elapsed "$NOW"
      PHASE="focus"; STARTED_AT=$NOW; ENDS_AT=$((NOW + FOCUS_MINUTES * 60)); NOTIFIED_FOR=0
      ;;
    break)
      bank_elapsed "$NOW"
      mins=$(break_minutes_for_count)
      PHASE="break"; STARTED_AT=$NOW; ENDS_AT=$((NOW + mins * 60)); NOTIFIED_FOR=0
      ;;
    pause)
      bank_elapsed "$NOW"
      PHASE="idle"; STARTED_AT=0; ENDS_AT=0; NOTIFIED_FOR=0
      ;;
    reset)
      PHASE="idle"; STARTED_AT=0; ENDS_AT=0; NOTIFIED_FOR=0
      COUNT=0; FOCUS_SEC=0; BREAK_SEC=0
      ;;
    skip)
      bank_elapsed "$NOW"
      if [ "$PHASE" = "focus" ]; then
        mins=$(break_minutes_for_count)
        PHASE="break"; STARTED_AT=$NOW; ENDS_AT=$((NOW + mins * 60))
      else
        PHASE="focus"; STARTED_AT=$NOW; ENDS_AT=$((NOW + FOCUS_MINUTES * 60))
      fi
      NOTIFIED_FOR=0
      ;;
    *)
      : # unknown subcommand — no-op
      ;;
  esac

  save_state
  exit 0
fi

# ---------------------------------------------------------------------------
# Streaming mode — no arguments. Runs forever; Vee restarts it with backoff
# if it ever exits. Every tick is O(1) work (a few builtins, one `date`, a
# handful of `echo`s) so there is nothing here that grows unbounded, and the
# only subprocess (`date`, and rarely `open`) is short-lived and never
# forked more than once per second.
# ---------------------------------------------------------------------------

fmt_mmss() {
  local secs="$1"
  [ "$secs" -lt 0 ] && secs=0
  printf '%02d:%02d' $((secs / 60)) $((secs % 60))
}

urlencode() {
  local s="$1" out="" c i
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) out+="$c" ;;
      ' ') out+='%20' ;;
      *) out+=$(printf '%%%02X' "'$c") ;;
    esac
  done
  printf '%s' "$out"
}

# vee://notify — see ~/repos/vee/docs/_content/cli-and-urls.md#the-notify-action.
# `plugin=` makes the alert actionable (Re-run/Silence/Open Log) and
# coalesces repeats instead of stacking.
notify() {
  local title="$1" body="$2"
  open "vee://notify?plugin=$(urlencode "${VEE_PLUGIN_ID:-}")&title=$(urlencode "$title")&body=$(urlencode "$body")" \
    > /dev/null 2>&1
}

render() {
  local now="$1" remaining mm_ss color sfimg phase_label frac_num frac_den fmin bmin

  case "$PHASE" in
    focus)
      remaining=$((ENDS_AT - now)); [ "$remaining" -lt 0 ] && remaining=0
      color="orange"; [ "$remaining" -le 60 ] && color="red"
      sfimg="flame.fill"; phase_label="Focus"
      frac_num=$((now - STARTED_AT)); [ "$frac_num" -lt 0 ] && frac_num=0
      frac_den=$((ENDS_AT - STARTED_AT)); [ "$frac_den" -le 0 ] && frac_den=1
      ;;
    break)
      remaining=$((ENDS_AT - now)); [ "$remaining" -lt 0 ] && remaining=0
      color="green"
      sfimg="cup.and.saucer.fill"; phase_label="Break"
      frac_num=$((now - STARTED_AT)); [ "$frac_num" -lt 0 ] && frac_num=0
      frac_den=$((ENDS_AT - STARTED_AT)); [ "$frac_den" -le 0 ] && frac_den=1
      ;;
    *)
      remaining=0; color="gray"; sfimg="pause.circle"; phase_label="Idle"
      frac_num=0; frac_den=1
      ;;
  esac
  mm_ss=$(fmt_mmss "$remaining")

  echo "~~~"
  echo "${mm_ss} | sfimage=${sfimg} color=${color}"
  echo "---"
  echo "Phase: ${phase_label} | color=${color} sfimage=${sfimg}"
  echo "Phase progress | progress=${frac_num},${frac_den} color=${color} accessoryw=160 accessoryh=8"
  echo "Completed today: ${COUNT}"

  fmin=$((FOCUS_SEC / 60)); bmin=$((BREAK_SEC / 60))
  if [ $((fmin + bmin)) -gt 0 ]; then
    # A literal `|` in display text must be escaped as `\|` — the text
    # protocol's first unescaped `|` starts the param list.
    echo "Today: ${fmin}m focus \\| ${bmin}m break | stackedbar=${fmin},${bmin} chartlabels=Focus,Break chartcolors=orange,green accessoryw=180 accessoryh=14"
  else
    echo "Today: no time logged yet | color=gray"
  fi

  echo "---"
  echo "Start focus | shell=/bin/bash param0=${SCRIPT_PATH} param1=start sfimage=play.fill"
  echo "Start break | shell=/bin/bash param0=${SCRIPT_PATH} param1=break sfimage=cup.and.saucer sfcolor=green"
  echo "Pause | shell=/bin/bash param0=${SCRIPT_PATH} param1=pause sfimage=pause.fill"
  echo "Skip to next phase | shell=/bin/bash param0=${SCRIPT_PATH} param1=skip sfimage=forward.fill"
  echo "Reset | shell=/bin/bash param0=${SCRIPT_PATH} param1=reset sfimage=arrow.counterclockwise searchable=false"
}

while true; do
  read -r NOW TODAY < <(date +"%s %F")
  load_state
  check_day_rollover "$TODAY"

  # Natural phase completion: fires exactly once per (phase, ends_at) pair —
  # notified_for guards every later tick at the same ends_at from re-firing.
  if [ "$PHASE" != "idle" ] && [ "$NOW" -ge "$ENDS_AT" ] && [ "$NOTIFIED_FOR" != "$ENDS_AT" ]; then
    bank_elapsed "$NOW"
    if [ "$PHASE" = "focus" ]; then
      COUNT=$((COUNT + 1))
      title="Focus session complete"; body="Nice work — take a break."
    else
      title="Break's over"; body="Ready for another focus session?"
    fi
    NOTIFIED_FOR=$ENDS_AT
    save_state
    [ "$NOTIFY" = "true" ] && notify "$title" "$body"
  fi

  render "$NOW"
  sleep 1
done
