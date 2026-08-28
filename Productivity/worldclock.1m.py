#!/usr/bin/env python3
#
# worldclock.1m.py — time across the team, and whether it's a reasonable
# moment to message them.
#
# Reads TIMEZONES (a comma-separated list of "IANA/Zone=Label" pairs) and
# renders each one's current time, day offset from local, UTC offset, and a
# green/yellow/dim "can I message them right now" indicator based on their
# local hour. A "Meeting planner" submenu lists the next 12 hours per zone
# alongside local time, so overlap is a glance, not arithmetic. Every time
# row copies an ISO-8601 timestamp of that moment to the clipboard.
#
# Uses python3's stdlib `zoneinfo` (no `date`/TZ shelling) so the day-offset
# and DST math is exact rather than string-parsed — verified against a zone
# across the international date line (Etc/GMT+12) during authoring.
#
# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# <xbar.title>World Clock</xbar.title>
# <xbar.version>1.0</xbar.version>
# <xbar.author>Naveen Kumar</xbar.author>
# <xbar.author.github>navbytes</xbar.author.github>
# <xbar.desc>Team timezones with a working-hours indicator, a meeting-overlap planner, and click-to-copy timestamps.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.abouturl>https://github.com/navbytes/vee-plugins</xbar.abouturl>
#
# <xbar.var>string(TIMEZONES=Asia/Kolkata=Home,America/Los_Angeles=SF,Europe/London=London,Asia/Tokyo=Tokyo): Comma-separated Zone=Label pairs, e.g. "Asia/Tokyo=Tokyo,Europe/Paris=Paris".</xbar.var>
# <xbar.var>string(PRIMARY_ZONE=): IANA zone shown next to local time in the title. Empty shows local time only.</xbar.var>
#
# Trust declarations (advisory, never enforced):
# <vee.capabilities>clipboard</vee.capabilities>
# <vee.exec>pbcopy,sh</vee.exec>
# <vee.filesystem.read>/usr/share/zoneinfo</vee.filesystem.read>

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ZONEINFO_DIR = "/usr/share/zoneinfo"
TIMEZONES = os.environ.get(
    "TIMEZONES",
    "Asia/Kolkata=Home,America/Los_Angeles=SF,Europe/London=London,Asia/Tokyo=Tokyo",
)
PRIMARY_ZONE = os.environ.get("PRIMARY_ZONE", "").strip()


def emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.exit(0)


def zone_exists(name):
    # Validate against the zoneinfo tree before trusting ZoneInfo() to
    # succeed. Both checks matter: os.path.join() silently discards
    # ZONEINFO_DIR and returns just `name` when `name` is itself absolute
    # (so "/etc/passwd" would otherwise report as an existing "zone"), and a
    # ".." path component walks back out of the tree the same way. Either
    # one reaching ZoneInfo() raises an uncaught ValueError — this rejects
    # both up front so they land in the existing invalid-zone row instead.
    if not name or os.path.isabs(name):
        return False
    if ".." in name.split("/"):
        return False
    return os.path.exists(os.path.join(ZONEINFO_DIR, name))


def parse_timezones(raw):
    """Returns (valid [(zone, label), ...], invalid [raw_entry, ...])."""
    valid, invalid = [], []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        zone, _, label = entry.partition("=")
        zone = zone.strip()
        label = label.strip() or zone
        if zone and zone_exists(zone):
            valid.append((zone, label))
        else:
            invalid.append(entry)
    return valid, invalid


def utc_offset_str(dt):
    off = dt.utcoffset() or timedelta(0)
    total_min = int(off.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    total_min = abs(total_min)
    return f"UTC{sign}{total_min // 60:02d}:{total_min % 60:02d}"


def day_offset_str(zone_date, local_date):
    delta = (zone_date - local_date).days
    if delta == 0:
        return "same day"
    return f"{'+' if delta > 0 else '−'}{abs(delta)}d"


def working_hours_status(hour):
    # 09-18 green ("go ahead"), 07-09/18-22 yellow ("maybe"), else dim ("no").
    if 9 <= hour < 18:
        return "green", "sun.max.fill"
    if 7 <= hour < 9 or 18 <= hour < 22:
        return "yellow", "sun.haze.fill"
    return "gray", "moon.fill"


def copy_action(dt):
    """Copy an ISO-8601 timestamp of `dt` to the clipboard. pbcopy only reads
    stdin, so the click target is a tiny `/bin/sh -c` feeding it a heredoc —
    a pipe would work too, but a literal `|` inside a shell= command trips
    vee lint's text-protocol param scanner, which doesn't know a JSON
    `params` string is opaque shell text rather than a Vee param list."""
    iso = dt.replace(microsecond=0).isoformat()
    cmd = f"pbcopy <<'EOF'\n{iso}\nEOF"
    return {
        "shell": "/bin/sh",
        "params": ["-c", cmd],
        "tooltip": f"Copy {iso} to clipboard",
    }


def main():
    now = datetime.now(timezone.utc)
    local_now = now.astimezone()
    local_date = local_now.date()

    valid, invalid = parse_timezones(TIMEZONES)

    # --- Title -------------------------------------------------------------
    title_text = local_now.strftime("%H:%M")
    if PRIMARY_ZONE:
        primary_label = next((lbl for z, lbl in valid if z == PRIMARY_ZONE), None)
        if primary_label is None and zone_exists(PRIMARY_ZONE):
            primary_label = PRIMARY_ZONE.rsplit("/", 1)[-1]
        if primary_label is not None:
            primary_time = now.astimezone(ZoneInfo(PRIMARY_ZONE)).strftime("%H:%M")
            title_text = f"{title_text} · {primary_label} {primary_time}"

    if not valid and not invalid:
        emit(
            {
                "vee": 1,
                "title": [{"text": title_text, "sfimage": "clock"}],
                "items": [{"text": "No timezones configured (TIMEZONES is empty)", "color": "gray"}],
            }
        )

    # --- Team section --------------------------------------------------------
    items = [{"header": True, "text": "Team"}]
    for zone, label in valid:
        zdt = now.astimezone(ZoneInfo(zone))
        color, sfimage = working_hours_status(zdt.hour)
        row = {
            "text": f"{label} — {zdt.strftime('%H:%M')} ({day_offset_str(zdt.date(), local_date)}, {utc_offset_str(zdt)})",
            "color": color,
            "sfimage": sfimage,
            "tooltip": zone,
        }
        row.update(copy_action(zdt))
        items.append(row)
    for entry in invalid:
        items.append({"text": f"Invalid timezone: {entry}", "color": "gray", "disabled": True})

    # --- Meeting planner: next 12 hours per zone, aligned to local hours ---
    items.append({"separator": True})
    items.append({"header": True, "text": "Meeting planner"})
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    for zone, label in valid:
        submenu = []
        for i in range(12):
            t = hour_start + timedelta(hours=i)
            local_t = t.astimezone()
            zone_t = t.astimezone(ZoneInfo(zone))
            suffix = "" if zone_t.date() == local_t.date() else f" ({day_offset_str(zone_t.date(), local_t.date())})"
            row = {
                "text": f"{local_t.strftime('%H:%M')} local → {zone_t.strftime('%H:%M')} {label}{suffix}",
            }
            row.update(copy_action(zone_t))
            submenu.append(row)
        items.append({"text": f"Next 12h in {label}", "sfimage": "calendar", "submenu": submenu})

    emit(
        {
            "vee": 1,
            "title": [{"text": title_text, "sfimage": "clock"}],
            "items": items,
        }
    )


if __name__ == "__main__":
    main()
