#!/usr/bin/env python3
#
# network.30s.py -- where your packets are going.
#
# Written in Python 3 (see the shebang -- Apple ships it, so it costs no new
# dependency). The ".30s" in the filename only carries Vee's refresh
# interval convention; the shebang is what actually picks the interpreter.
# Python buys correct JSON escaping (json.dumps), a built-in subprocess
# timeout for the one system_profiler call, and simple file-based caching --
# all things that are fiddly to get right by hand in bash.
#
# What it shows:
#   - Menu-bar title: SF Symbol for the active (default-route) interface --
#     wifi / cable / none -- plus the SSID or interface name, colored by
#     Wi-Fi signal quality when applicable.
#   - Wi-Fi section: SSID, RSSI/noise as a signal-quality progress bar,
#     channel, PHY mode, tx rate -- from ONE `system_profiler
#     SPAirPortDataType -json` call (guarded by a timeout), since `airport -I`
#     is gone on modern macOS. Degrades to an SSID-only row (via
#     `networksetup -getairportnetwork`) when that data isn't available.
#   - Addresses: local IPv4/IPv6, gateway, first-resolver DNS servers for the
#     active interface. Each address row copies itself to the clipboard.
#   - Public IP (opt-in, SHOW_PUBLIC_IP): one `curl` to api.ipify.org, cached
#     10 minutes on disk so a 30s-interval plugin doesn't hammer the API.
#   - Latency (opt-in, PING_ENABLED): a sparkline of the last 20 round-trips
#     to PING_HOST, one `ping -c 1 -t 2` per run appended to a cache file.
#   - Links to open Network settings and Wireless Diagnostics.
#   - No Wi-Fi power toggle -- too easy to lock yourself out of a remote
#     session.
#
# <xbar.title>Network</xbar.title>
# <xbar.version>1.0</xbar.version>
# <xbar.author>Naveen Kumar</xbar.author>
# <xbar.author.github>navbytes</xbar.author.github>
# <xbar.desc>Active interface, Wi-Fi signal, addresses, and latency, with opt-in public IP lookup.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.abouturl>https://github.com/navbytes/vee-plugins</xbar.abouturl>
#
# <xbar.var>boolean(SHOW_PUBLIC_IP=false): Look up your public IP via api.ipify.org. Makes a network call, cached 10 minutes.</xbar.var>
# <xbar.var>boolean(PING_ENABLED=true): Track latency to PING_HOST with a sparkline (one ping per refresh).</xbar.var>
# <xbar.var>string(PING_HOST=1.1.1.1): Host pinged for the latency sparkline.</xbar.var>
#
# Trust declarations (advisory, never enforced -- see docs/trust-model.md):
# <vee.capabilities>network,filesystem,exec,clipboard</vee.capabilities>
# <vee.network>api.ipify.org (opt-in public IP lookup, SHOW_PUBLIC_IP), PING_HOST -- default 1.1.1.1, user-configurable (opt-in latency ping, PING_ENABLED)</vee.network>
# <vee.exec>networksetup, system_profiler, ipconfig, ifconfig, route, scutil, ping, curl, pbcopy, open, python3</vee.exec>
# <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/network-ping-history.txt, $SWIFTBAR_PLUGIN_CACHE_PATH/network-public-ip.json</vee.filesystem.write>

import json
import os
import re
import subprocess
import sys
import time

CACHE_DIR = os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH") or os.environ.get("TMPDIR", "/tmp")
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except OSError:
    pass

PING_HOST = (os.environ.get("PING_HOST") or "1.1.1.1").strip() or "1.1.1.1"
PING_ENABLED = (os.environ.get("PING_ENABLED") or "true").strip().lower() == "true"
SHOW_PUBLIC_IP = (os.environ.get("SHOW_PUBLIC_IP") or "false").strip().lower() == "true"

PING_CACHE = os.path.join(CACHE_DIR, "network-ping-history.txt")
IP_CACHE = os.path.join(CACHE_DIR, "network-public-ip.json")
IP_CACHE_TTL = 600  # seconds


def run(cmd, timeout=3):
    """Run an external command, returning stdout or "" on any failure/timeout."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Interface discovery
# ---------------------------------------------------------------------------

def get_wifi_device():
    out = run(["/usr/sbin/networksetup", "-listallhardwareports"])
    for block in out.split("\n\n"):
        if "Hardware Port: Wi-Fi" in block:
            m = re.search(r"Device: (\S+)", block)
            if m:
                return m.group(1)
    return "en0"


def get_hardware_port_map():
    """Device -> human-readable hardware port name, e.g. en4 -> Ethernet Adapter (en4)."""
    out = run(["/usr/sbin/networksetup", "-listallhardwareports"])
    mapping = {}
    for block in out.split("\n\n"):
        pm = re.search(r"Hardware Port: (.+)", block)
        dm = re.search(r"Device: (\S+)", block)
        if pm and dm:
            mapping[dm.group(1)] = pm.group(1).strip()
    return mapping


def get_default_interface():
    out = run(["/sbin/route", "-n", "get", "default"])
    m = re.search(r"interface: (\S+)", out)
    return m.group(1) if m else ""


def get_gateway():
    out = run(["/sbin/route", "-n", "get", "default"])
    m = re.search(r"gateway: (\S+)", out)
    return m.group(1) if m else ""


def get_local_ipv4(iface):
    return run(["/usr/sbin/ipconfig", "getifaddr", iface]).strip()


def get_local_ipv6(iface):
    out = run(["/sbin/ifconfig", iface])
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("inet6 "):
            addr = line.split()[1]
            if not addr.startswith("fe80"):  # skip link-local
                return addr
    return ""


def get_dns_servers():
    """First resolver set only, per scutil --dns's own ordering."""
    out = run(["/usr/sbin/scutil", "--dns"])
    servers = []
    in_resolver1 = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("resolver #1"):
            in_resolver1 = True
            continue
        if s.startswith("resolver #"):
            if in_resolver1:
                break
            continue
        if in_resolver1 and s.startswith("nameserver["):
            servers.append(s.split(":", 1)[1].strip())
    return servers


def get_ssid_fallback(wifi_device):
    out = run(["/usr/sbin/networksetup", "-getairportnetwork", wifi_device])
    m = re.search(r"Current Wi-Fi Network: (.+)", out)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Wi-Fi detail: ONE system_profiler call, guarded by a timeout, everything
# else parsed from that single invocation.
# ---------------------------------------------------------------------------

def get_wifi_details(wifi_device):
    out = run(["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"], timeout=4)
    if not out:
        return None
    try:
        data = json.loads(out)
        interfaces = data.get("SPAirPortDataType", [{}])[0].get("spairport_airport_interfaces", [])
        for iface in interfaces:
            if iface.get("_name") != wifi_device:
                continue
            status = iface.get("spairport_status_information", "")
            info = iface.get("spairport_current_network_information")
            if not info or "connected" not in status:
                return None
            sn = info.get("spairport_signal_noise", "")
            m = re.match(r"(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", sn)
            return {
                "ssid": info.get("_name", ""),
                "channel": info.get("spairport_network_channel", ""),
                "phymode": info.get("spairport_network_phymode", ""),
                "rate": info.get("spairport_network_rate", ""),
                "rssi": int(m.group(1)) if m else None,
                "noise": int(m.group(2)) if m else None,
            }
        return None
    except Exception:
        return None


def signal_progress(rssi):
    """Map RSSI -90..-30 dBm onto a 0..1 completion fraction."""
    if rssi is None:
        return None
    return max(0.0, min(1.0, (rssi - (-90)) / ((-30) - (-90))))


def signal_color(progress):
    if progress is None:
        return "blue"
    if progress >= 0.66:
        return "green"
    if progress >= 0.33:
        return "orange"
    return "red"


# ---------------------------------------------------------------------------
# Clipboard-copying address row: shell + params, no string interpolation
# into the shell script itself -- the value travels as a positional arg.
# Deliberately no shell pipe here (a bare `python3 -c ... | pbcopy` would
# work too, but a literal "|" inside a JSON string value trips up `vee
# lint`'s JSON detection -- feeding pbcopy via subprocess.run(input=...)
# sidesteps that entirely).
# ---------------------------------------------------------------------------

def copy_row(label, value):
    if not value:
        return {"text": f"{label}: —", "color": "gray", "disabled": True}
    return {
        "text": f"{label}: {value}",
        "tooltip": f"Click to copy {value} to the clipboard",
        "shell": "/usr/bin/python3",
        "params": [
            "-c",
            "import subprocess,sys; subprocess.run(['/usr/bin/pbcopy'], input=sys.argv[1].encode())",
            value,
        ],
        "terminal": False,
    }


# ---------------------------------------------------------------------------
# Public IP: opt-in, cached on disk for IP_CACHE_TTL seconds.
# ---------------------------------------------------------------------------

def get_public_ip():
    now = time.time()
    cached = None
    try:
        with open(IP_CACHE) as f:
            cached = json.load(f)
    except Exception:
        cached = None

    if cached and now - cached.get("ts", 0) < IP_CACHE_TTL:
        return cached.get("ip", ""), True

    out = run(["/usr/bin/curl", "--max-time", "4", "-sS", "https://api.ipify.org"], timeout=6)
    ip = out.strip()
    if ip:
        try:
            with open(IP_CACHE, "w") as f:
                json.dump({"ts": now, "ip": ip}, f)
        except Exception:
            pass
        return ip, False

    # Lookup failed this run -- fall back to a stale cached value if we have one.
    if cached:
        return cached.get("ip", ""), True
    return "", False


# ---------------------------------------------------------------------------
# Latency: opt-in, one ping per run, appended to a rolling 20-entry cache.
# ---------------------------------------------------------------------------

def load_ping_history():
    try:
        with open(PING_CACHE) as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception:
        lines = []
    values = []
    for line in lines:
        try:
            values.append(float(line))
        except ValueError:
            pass
    return values[-20:]


def ping_once(host):
    out = run(["/sbin/ping", "-c", "1", "-t", "2", host], timeout=3)
    m = re.search(r"time=([\d.]+)", out)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Build the menu
# ---------------------------------------------------------------------------

wifi_device = get_wifi_device()
hw_map = get_hardware_port_map()
default_if = get_default_interface()
is_wifi_active = bool(default_if) and default_if == wifi_device
wifi_details = get_wifi_details(wifi_device)
ssid_fallback = get_ssid_fallback(wifi_device)

# --- Title -------------------------------------------------------------
if is_wifi_active:
    if wifi_details and wifi_details.get("rssi") is not None:
        progress = signal_progress(wifi_details["rssi"])
        title_color = signal_color(progress)
        title_text = wifi_details["ssid"] or ssid_fallback or "Wi-Fi"
    else:
        title_color = "blue"
        title_text = ssid_fallback or "Wi-Fi"
    title = [{"text": title_text, "sfimage": "wifi", "color": title_color}]
elif default_if:
    title = [{"text": hw_map.get(default_if, default_if), "sfimage": "cable.connector", "color": "green"}]
else:
    title = [{"text": "No network", "sfimage": "wifi.slash", "color": "red"}]

items = []

# --- Wi-Fi section (always shown when Wi-Fi is associated, even if it is
# not the interface carrying the default route) -------------------------
items.append({"header": True, "text": "Wi-Fi"})
if wifi_details and wifi_details.get("rssi") is not None:
    progress = signal_progress(wifi_details["rssi"])
    items.append({"text": f"SSID: {wifi_details['ssid']}"})
    items.append({
        "text": f"Signal: {wifi_details['rssi']} dBm (noise {wifi_details['noise']} dBm)",
        "color": signal_color(progress),
        "progress": progress,
        "progressWidth": 120,
        "progressHeight": 6,
        "tooltip": "RSSI mapped onto -90..-30 dBm",
    })
    items.append({"text": f"Channel: {wifi_details['channel']}"})
    items.append({"text": f"PHY mode: {wifi_details['phymode']}"})
    rate = wifi_details["rate"]
    items.append({"text": f"Tx rate: {rate} Mbps" if rate != "" else "Tx rate: unknown"})
elif ssid_fallback:
    items.append({"text": f"SSID: {ssid_fallback}"})
    items.append({
        "text": "Signal details unavailable (system_profiler didn't report them) -- showing SSID only.",
        "color": "gray",
    })
else:
    items.append({"text": "Not connected to Wi-Fi", "color": "gray"})

# --- Addresses -----------------------------------------------------------
items.append({"separator": True})
items.append({"header": True, "text": "Addresses"})
if default_if:
    items.append(copy_row(f"IPv4 ({default_if})", get_local_ipv4(default_if)))
    items.append(copy_row(f"IPv6 ({default_if})", get_local_ipv6(default_if)))
    items.append(copy_row("Gateway", get_gateway()))
    dns_servers = get_dns_servers()
    if dns_servers:
        for i, server in enumerate(dns_servers, start=1):
            items.append(copy_row(f"DNS {i}", server))
    else:
        items.append({"text": "DNS: none found", "color": "gray"})
else:
    items.append({"text": "No active network connection", "color": "gray"})

# --- Public IP (opt-in) ---------------------------------------------------
items.append({"separator": True})
items.append({"header": True, "text": "Public IP"})
if SHOW_PUBLIC_IP:
    ip, from_cache = get_public_ip()
    if ip:
        row = copy_row("Public IP", ip)
        if from_cache:
            row["tooltip"] += " (cached, refreshes every 10 min)"
        items.append(row)
    else:
        items.append({"text": "Public IP lookup failed (api.ipify.org unreachable)", "color": "orange"})
else:
    items.append({"text": "Public IP lookup is off", "color": "gray"})
    items.append({"text": "Enable SHOW_PUBLIC_IP in plugin Settings to check api.ipify.org", "color": "gray"})

# --- Latency (opt-in) ------------------------------------------------------
items.append({"separator": True})
items.append({"header": True, "text": "Latency"})
if PING_ENABLED:
    rtt = ping_once(PING_HOST)
    history = load_ping_history()
    if rtt is not None:
        history.append(rtt)
        history = history[-20:]
        try:
            with open(PING_CACHE, "w") as f:
                f.write("\n".join(f"{v:.2f}" for v in history) + "\n")
        except Exception:
            pass
    else:
        items.append({"text": f"Ping to {PING_HOST} timed out", "color": "red"})

    if history:
        latest = history[-1]
        if latest < 50:
            lat_color = "green"
        elif latest < 150:
            lat_color = "orange"
        else:
            lat_color = "red"
        items.append({
            "text": f"Current: {latest:.0f} ms to {PING_HOST}",
            "color": lat_color,
        })
        items.append({
            "text": "History",
            "sparkline": history,
            "sparklineColor": lat_color,
            "accessoryWidth": 140,
            "accessoryHeight": 20,
            "tooltip": f"Last {len(history)} round-trips to {PING_HOST}",
        })
    elif rtt is None:
        items.append({"text": "No round-trips recorded yet", "color": "gray"})
else:
    items.append({"text": "Latency tracking is off", "color": "gray"})
    items.append({"text": f"Enable PING_ENABLED in plugin Settings to ping {PING_HOST}", "color": "gray"})

# --- Actions ---------------------------------------------------------------
items.append({"separator": True})
items.append({
    "text": "Open Network Settings",
    "sfimage": "gearshape",
    "shell": "/usr/bin/open",
    "params": ["/System/Library/PreferencePanes/Network.prefPane"],
    "terminal": False,
})
items.append({
    "text": "Open Wireless Diagnostics",
    "sfimage": "stethoscope",
    "shell": "/usr/bin/open",
    "params": ["-a", "Wireless Diagnostics"],
    "terminal": False,
})
items.append({"text": "Refresh", "refresh": True, "sfimage": "arrow.clockwise"})

print(json.dumps({"vee": 1, "title": title, "items": items}))
sys.stdout.flush()
