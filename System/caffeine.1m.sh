#!/bin/bash
# <vee.title>Caffeine</vee.title>
# <vee.version>1.1</vee.version>
# <vee.author>Naveen Kumar</vee.author>
# <vee.author.github>navbytes</vee.author.github>
# <vee.desc>Keep your Mac awake — indefinitely or for a timed session — from the menu bar.</vee.desc>
# <vee.dependencies>caffeinate</vee.dependencies>
# <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
#
# <vee.exec>caffeinate,kill,ps,pgrep,date,rm,mkdir,rmdir,nohup,tr,tail,mv,wc</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_DATA_PATH (falls back to $SWIFTBAR_PLUGIN_CACHE_PATH, then $TMPDIR): vee-caffeine.state, vee-caffeine.log, vee-caffeine.state.lock/</vee.filesystem.write>
#
# A menu-bar front-end for macOS `caffeinate`. Clicking a menu item re-invokes
# this script with an action argument; the render pass (no args) draws the menu.
# 1m interval so the "time left" title actually counts down (humanize()'s
# granularity bottoms out at minutes, so 1m matches it exactly).
#
# State lives in a stable per-plugin directory, not $TMPDIR: macOS reaps
# $TMPDIR periodically and it differs per login session, which used to orphan
# a running `caffeinate` the moment its state file vanished (it kept running,
# forgotten, with no way to stop it from this menu).

set -euo pipefail

PLUGIN="${VEE_PLUGIN_PATH:-${SWIFTBAR_PLUGIN_PATH:-$0}}"
BASE_DIR="${SWIFTBAR_PLUGIN_DATA_PATH:-${SWIFTBAR_PLUGIN_CACHE_PATH:-${TMPDIR:-/tmp}}}"
BASE_DIR="${BASE_DIR%/}"
mkdir -p "$BASE_DIR" 2>/dev/null || true

STATE="${BASE_DIR}/vee-caffeine.state"   # "PID ENDTIME DUR START MODE" (ENDTIME/DUR 0 = indefinite)
LOG="${BASE_DIR}/vee-caffeine.log"
LOCKD="${STATE}.lock"
LOCK_STALE_SECS=15   # comfortably above any legitimate critical section (a fork + a write)
LOG_MAX_BYTES=131072  # 128 KiB — ponytail: size-triggered trim, not a rotating-log lib

AMBER="#F5A623"
DIM="#8A8F98"

now() { date +%s; }
log() {
  echo "$(date '+%H:%M:%S') $*" >> "$LOG"
  if [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    tail -c "$LOG_MAX_BYTES" "$LOG" > "${LOG}.tmp" 2>/dev/null && mv -f "${LOG}.tmp" "$LOG"
  fi
}

# ── Lock (atomic via mkdir, owner-checked, self-healing) ─────────────────────
# A bare mkdir-lock with no owner wedges forever the moment its holder is
# SIGKILLed (Vee's own execution-timeout behavior) instead of hitting the EXIT
# trap that releases it. Recording the owner PID lets us tell "held" from
# "abandoned" and break the latter automatically instead of requiring someone
# to know to rmdir a path in $TMPDIR by hand.
acquire() {
  if mkdir "$LOCKD" 2>/dev/null; then
    echo $$ > "${LOCKD}/pid" 2>/dev/null || true
    return 0
  fi
  local owner age
  owner="$(cat "${LOCKD}/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && is_uint "$owner" && kill -0 "$owner" 2>/dev/null; then
    age=$(( $(now) - $(date -r "$LOCKD" +%s 2>/dev/null || echo "$(now)") ))
    [ "$age" -lt "$LOCK_STALE_SECS" ] && return 1   # genuinely held
  fi
  # Stale: owner missing, owner's process is gone, or held far longer than any
  # action here should take. Break it and take over.
  # ponytail: mkdir-then-pid-write has a microsecond race window between two
  # concurrent acquirers; not worth cross-process locking for a menu click.
  log "acquire: breaking stale lock (owner=${owner:-none})"
  rm -rf "$LOCKD" 2>/dev/null
  mkdir "$LOCKD" 2>/dev/null || return 1
  echo $$ > "${LOCKD}/pid" 2>/dev/null || true
  return 0
}
release() { rm -f "${LOCKD}/pid" 2>/dev/null; rmdir "$LOCKD" 2>/dev/null || true; }

# ── Guards ────────────────────────────────────────────────────────────────────
is_uint() { case "$1" in ''|*[!0-9]*) return 1;; esac; return 0; }

# Real start time of a PID, as a unix epoch — used to tell "the caffeinate we
# started" apart from an unrelated process that later recycled the same PID.
# Forced LC_ALL=C because ps's lstart= wording (day/month order) is locale-
# dependent and would otherwise break the date -j parse on non-C locales.
proc_start_epoch() {
  local pid="$1" raw
  raw="$(LC_ALL=C ps -p "$pid" -o lstart= 2>/dev/null)" || return 1
  [ -n "$raw" ] || return 1
  raw="$(echo "$raw" | tr -s ' ')"
  raw="${raw# }"; raw="${raw% }"
  date -j -f "%a %b %e %T %Y" "$raw" +%s 2>/dev/null
}

# $1=pid $2=expected start epoch → true iff that PID is still a `caffeinate`
# process that started at that exact moment (name alone isn't enough: a
# recycled PID could belong to a different caffeinate invocation entirely).
caffeinate_matches() {
  local pid="${1:-}" want="${2:-}"
  [ -n "$pid" ] && is_uint "$pid" || return 1
  [ "$(ps -p "$pid" -o comm= 2>/dev/null | tr -d ' ')" = "caffeinate" ] || return 1
  [ -n "$want" ] && is_uint "$want" || return 1
  [ "$(proc_start_epoch "$pid")" = "$want" ]
}

# Every `caffeinate` PID on the machine other than $1, as "PID START CMD"
# lines — this plugin never assumes a stray belongs to it; it shows the full
# command so the user can tell theirs apart from some other tool's assertion.
list_strays() {
  local mine="${1:-}" pid start cmd
  for pid in $(pgrep -x caffeinate 2>/dev/null); do
    [ "$pid" = "$mine" ] && continue
    start="$(proc_start_epoch "$pid")" || continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null | tr -d '\n')"
    echo "${pid} ${start} ${cmd}"
  done
}

# ── State helpers ─────────────────────────────────────────────────────────────
read_state() {  # sets PID ENDTIME DUR START MODE, or returns 1 if inactive (pure query)
  [ -f "$STATE" ] || return 1
  read -r PID ENDTIME DUR START MODE < "$STATE" || return 1
  caffeinate_matches "${PID:-}" "${START:-}" || return 1
  if [ "${ENDTIME:-0}" -gt 0 ] && [ "$(now)" -ge "$ENDTIME" ]; then return 1; fi
  return 0
}

clear_state() {
  if [ -f "$STATE" ]; then
    read -r P _ _ S _ < "$STATE" 2>/dev/null || true
    if caffeinate_matches "${P:-}" "${S:-}"; then
      kill "$P" 2>/dev/null || true
      log "clear_state killed caffeinate PID=$P"
    else
      log "clear_state: PID=${P:-?} not a matching caffeinate, skip kill"
    fi
  fi
  rm -f "$STATE"
}

start_session() {  # $1=duration seconds (0=indefinite) $2=mode (dimsu|is)
  local secs="$1" mode="$2" pid start
  acquire || { log "start_session: lock held, exit"; exit 0; }
  trap 'release' EXIT
  clear_state
  local -a flags
  if [ "$mode" = "is" ]; then flags=(-is); else flags=(-dimsu); fi
  if [ "$secs" -gt 0 ]; then
    nohup caffeinate "${flags[@]}" -t "$secs" >/dev/null 2>&1 &
  else
    nohup caffeinate "${flags[@]}" >/dev/null 2>&1 &
  fi
  pid=$!
  start="$(proc_start_epoch "$pid")"; start="${start:-$(now)}"
  if [ "$secs" -gt 0 ]; then
    echo "$pid $(( $(now) + secs )) $secs $start $mode" > "$STATE"
  else
    echo "$pid 0 0 $start $mode" > "$STATE"
  fi
  log "start_session: secs=$secs mode=$mode PID=$pid"
  release
}

# ── Human-readable duration ───────────────────────────────────────────────────
humanize() {  # $1 = seconds → "1h 23m" / "45m" / "30s"
  local s=$1 h m
  h=$(( s / 3600 )); m=$(( (s % 3600) / 60 ))
  if [ "$h" -gt 0 ]; then printf "%dh %dm" "$h" "$m"
  elif [ "$m" -gt 0 ]; then printf "%dm" "$m"
  else printf "%ds" "$s"; fi
}

# ── Actions (invoked from clicked items) ──────────────────────────────────────
case "${1:-}" in
  start)
    is_uint "${2:-}" || { log "start: invalid arg=${2:-}"; exit 0; }
    mode="${3:-dimsu}"; case "$mode" in dimsu|is) ;; *) mode=dimsu ;; esac
    start_session "${2:-0}" "$mode"; exit 0 ;;
  startmin)
    local_min="${2:-${VEE_CONTROL_VALUE:-0}}"
    is_uint "$local_min" || { log "startmin: invalid arg=$local_min"; exit 0; }
    [ "$local_min" -gt 0 ] || { log "startmin: 0 rejected"; exit 0; }
    start_session "$(( local_min * 60 ))" "dimsu"; exit 0 ;;
  stop)
    acquire || { log "stop: lock held, exit"; exit 0; }
    trap 'release' EXIT
    log "stop"
    clear_state
    release
    exit 0 ;;
  adopt)  # $2=pid $3=start-epoch — start tracking an already-running stray as an indefinite session
    is_uint "${2:-}" && is_uint "${3:-}" || { log "adopt: invalid args"; exit 0; }
    caffeinate_matches "$2" "$3" || { log "adopt: PID=$2 no longer matches, skip"; exit 0; }
    acquire || { log "adopt: lock held, exit"; exit 0; }
    trap 'release' EXIT
    clear_state
    echo "$2 0 0 $3 unknown" > "$STATE"
    log "adopt: PID=$2 now tracked"
    release
    exit 0 ;;
  killstray)  # $2=pid $3=start-epoch — kill one specific caffeinate, verified by PID+start time
    is_uint "${2:-}" && is_uint "${3:-}" || { log "killstray: invalid args"; exit 0; }
    if caffeinate_matches "$2" "$3"; then
      kill "$2" 2>/dev/null || true
      log "killstray: killed PID=$2"
    else
      log "killstray: PID=$2 no longer matches, skip"
    fi
    exit 0 ;;
esac

# ── Render ────────────────────────────────────────────────────────────────────
PRESETS=( "15 minutes:900" "30 minutes:1800" "1 hour:3600" "2 hours:7200" "4 hours:14400" )

if read_state; then
  ACTIVE=1
  ENDTIME="${ENDTIME:-0}"; DUR="${DUR:-0}"; MODE="${MODE:-dimsu}"
  case "$ENDTIME" in ''|*[!0-9]*) ENDTIME=0;; esac
  case "$DUR" in ''|*[!0-9]*) DUR=0;; esac
  if [ "$ENDTIME" -gt 0 ]; then
    LEFT=$(( ENDTIME - $(now) )); [ "$LEFT" -lt 0 ] && LEFT=0
    REMAIN="$(humanize "$LEFT")"
  else
    REMAIN=""
  fi
else
  ACTIVE=0
fi

# Menu-bar title: filled warm cup when awake, quiet outline cup when sleeping.
if [ "$ACTIVE" -eq 1 ]; then
  echo "${REMAIN} | sfimage=cup.and.saucer.fill sfcolor=${AMBER}"
else
  echo " | sfimage=cup.and.saucer sfcolor=${DIM}"
fi

echo "---"

# Status header + primary toggle.
if [ "$ACTIVE" -eq 1 ]; then
  MODE_TXT="display+system awake"
  [ "$MODE" = "is" ] && MODE_TXT="system awake, display may sleep"
  if [ -n "$REMAIN" ]; then
    echo "Awake (${MODE_TXT}) — ${REMAIN} left | sfimage=cup.and.saucer.fill sfcolor=${AMBER} disabled=true"
    echo "Sleeps at $(date -r "$ENDTIME" '+%-I:%M %p') | size=11 color=${DIM} disabled=true"
  else
    echo "Awake (${MODE_TXT}) — until you stop | sfimage=infinity sfcolor=${AMBER} disabled=true"
  fi
  echo "---"
  echo "Let it sleep now | sfimage=moon.zzz.fill sfcolor=${DIM} shell=\"${PLUGIN}\" param0=stop refresh=true"
else
  echo "Sleep allowed | sfimage=moon.zzz sfcolor=${DIM} disabled=true"
fi

echo "---"
checked_dimsu=""; checked_is=""
if [ "$ACTIVE" -eq 1 ] && [ "$ENDTIME" -eq 0 ]; then
  [ "${MODE:-dimsu}" = "is" ] && checked_is=" checked=true" || checked_dimsu=" checked=true"
fi
echo "Keep display + system awake — until I stop | sfimage=cup.and.saucer.fill sfcolor=${AMBER} shell=\"${PLUGIN}\" param0=start param1=0 param2=dimsu refresh=true${checked_dimsu}"
echo "Keep system awake, allow display sleep — until I stop | sfimage=moon.stars sfcolor=${AMBER} shell=\"${PLUGIN}\" param0=start param1=0 param2=is refresh=true${checked_is}"

echo "---"
echo "Timed session | size=11 color=${DIM} disabled=true"
for p in "${PRESETS[@]}"; do
  label="${p%%:*}"; secs="${p##*:}"
  checked=""
  [ "$ACTIVE" -eq 1 ] && [ "${DUR:-0}" -eq "$secs" ] && checked=" checked=true"
  echo "${label} | sfimage=timer shell=\"${PLUGIN}\" param0=start param1=${secs} refresh=true${checked}"
done
seedmin=60
if [ "$ACTIVE" -eq 1 ] && [ "${DUR:-0}" -gt 0 ]; then
  seedmin=$(( DUR / 60 ))
  [ "$seedmin" -lt 5 ] && seedmin=5
  [ "$seedmin" -gt 240 ] && seedmin=240
fi
echo "Custom duration… | sfimage=slider.horizontal.3 slider=5,240,${seedmin} shell=\"${PLUGIN}\" param0=startmin refresh=true"

# Strays: every caffeinate on the machine this plugin isn't tracking, shown
# with its start time and full command so the user — not a blanket pkill —
# decides what to stop. Also how an orphaned session (state file gone, process
# still running) gets back under this menu's control.
STRAYS="$(list_strays "${PID:-}")"
if [ -n "$STRAYS" ]; then
  echo "---"
  echo "Other caffeinate processes (not tracked here) | size=11 color=${DIM} disabled=true"
  while read -r spid sstart scmd; do
    [ -n "$spid" ] || continue
    scmd="${scmd//|/\\|}"
    echo "PID ${spid} · started $(date -r "$sstart" '+%-I:%M %p') · ${scmd} | sfimage=exclamationmark.triangle sfcolor=${AMBER}"
    echo "-- Take control (track and manage here) | sfimage=hand.raised shell=\"${PLUGIN}\" param0=adopt param1=${spid} param2=${sstart} refresh=true"
    echo "-- Stop this session | sfimage=xmark.octagon shell=\"${PLUGIN}\" param0=killstray param1=${spid} param2=${sstart} refresh=true searchable=false"
  done <<< "$STRAYS"
fi
