#!/usr/bin/env bash

# Investigate an unknown device on the LAN.
# Usage:
#   ./device_probe.sh -ip4 192.168.10.108
#   ./device_probe.sh -ip6 2001:db8::1234
#   ./device_probe.sh -mac 6c:47:60:41:96:dd
#
# Optional env:
#   USE_SUDO=1           # use sudo for nmap (if configured passwordless)
#   NMAP=/usr/bin/nmap   # override nmap path
#   TIMEOUT_S=3          # command timeout seconds (where supported)
#   LEASES_FILE=...      # AdGuard/your DHCP leases JSON file
#   STATE_FILE=...       # device watcher state JSON (defaults in watcher)

set -o pipefail

TIMEOUT_S=${TIMEOUT_S:-3}
NMAP_BIN="/usr/bin/nmap"
USE_SUDO="1"
LEASES_FILE="/opt/AdGuardHome/data/leases.json"
STATE_FILE="/tmp/presence_state.json"

have() { command -v "$1" >/dev/null 2>&1; }

maybe_sudo() {
  if [ "${USE_SUDO}" = "1" ]; then
    echo sudo
  else
    echo
  fi
}

run() {
  # run <label> <cmd...>
  local label="$1"; shift
  echo "-- ${label}"
  if have timeout; then
    timeout -k 1 "${TIMEOUT_S}" "$@" 2>&1 | sed 's/^/   /'
  else
    "$@" 2>&1 | sed 's/^/   /'
  fi
}

print_section() {
  echo
  echo "=================================================="
  echo "$1"
  echo "=================================================="
}

ttl_guess_os() {
  # Input: TTL value, Output: OS hint
  local ttl="$1"
  if [ -z "$ttl" ]; then echo "unknown"; return; fi
  if [ "$ttl" -ge 240 ]; then echo "network device (TTL~255)"; return; fi
  if [ "$ttl" -ge 120 ] && [ "$ttl" -le 135 ]; then echo "likely Windows (TTL~128)"; return; fi
  if [ "$ttl" -ge 58 ] && [ "$ttl" -le 70 ]; then echo "Linux/Unix/macOS (TTL~64)"; return; fi
  echo "unknown"
}

rdns_lookup() {
  local ip="$1"
  local out=""
  if have dig; then
    out=$(dig +short -x "$ip" 2>/dev/null | sed -n '1{s/\.$//;p}')
  fi
  if [ -z "$out" ]; then
    if have getent; then
      out=$(getent hosts "$ip" | awk '{print $2}' | sed -n '1{s/\.$//;p}')
    fi
  fi
  if [ -z "$out" ] && have python3; then
    out=$(python3 - <<PY
import socket, sys
ip=sys.argv[1]
try:
  h=socket.gethostbyaddr(ip)[0].rstrip('.')
  print(h)
except Exception:
  pass
PY
"$ip")
  fi
  echo "$out"
}

mac_upper_oui() {
  echo "$1" | tr '[:lower:]' '[:upper:]' | awk -F: '{printf "%s:%s:%s\n",$1,$2,$3}'
}

mac_vendor_lookup() {
  local mac="$1"
  local oui=$(mac_upper_oui "$mac")
  # Try Wireshark manuf first
  if [ -r /usr/share/wireshark/manuf ]; then
    grep -i -m 1 "^${oui}[[:space:]]" /usr/share/wireshark/manuf | sed 's/^/   /'
    return
  fi
  # Try ieee oui data
  for f in /usr/share/ieee-data/oui.txt /usr/share/misc/oui.txt /var/lib/ieee-data/oui.txt; do
    if [ -r "$f" ]; then
      # Lines contain forms like: 001122     (hex)        Vendor Name
      local hex=$(echo "$oui" | tr -d ':')
      grep -i -m 1 "^${hex}[[:space:]]\+(hex\)\?" "$f" | sed 's/^/   /'
      return
    fi
  done
}

dhcp_lookup() {
  local filter_key="$1"; shift
  local filter_val="$1"; shift
  [ -n "$LEASES_FILE" ] && [ -r "$LEASES_FILE" ] || return 0
  if have python3; then
    python3 - "$LEASES_FILE" "$filter_key" "$filter_val" <<'PY'
import sys, json
path, key, val = sys.argv[1:]
try:
  data=json.load(open(path))
except Exception as e:
  print("   failed reading leases:", e)
  sys.exit(0)
leases=data.get('leases', [])
for it in leases:
  if str(it.get(key, '')).strip().lower()==val.lower():
    print("   match:")
    print("     ip=", it.get('ip',''))
    print("     mac=", it.get('mac',''))
    print("     hostname=", it.get('hostname',''))
    print("     static=", it.get('static', False))
    print("     expires=", it.get('expires',''))
PY
  fi
}

state_lookup() {
  local ip="$1"; local mac="$2"
  [ -r "$STATE_FILE" ] || return 0
  if have python3; then
    python3 - "$STATE_FILE" "$ip" "$mac" <<'PY'
import sys, json
path, ip, mac = sys.argv[1:]
try:
  st=json.load(open(path))
except Exception as e:
  print("   failed reading state:", e)
  sys.exit(0)
for k,v in st.items():
  if (ip and v.get('ip')==ip) or (mac and v.get('mac','').lower()==mac.lower()):
    print("   seen:")
    print("     key=", k)
    print("     first_seen=", v.get('first_seen',''))
    print("     hostname=", v.get('hostname',''))
    print("     rdns=", v.get('rdns',''))
    print("     source=", v.get('source',''))
PY
  fi
}

probe_ip4() {
  local ip="$1"
  print_section "IPv4 Target: $ip"

  local rdns=$(rdns_lookup "$ip")
  echo "rDNS: ${rdns:-<none>}"

  if have ping; then
    local pout=$(ping -n -c 1 -W "$TIMEOUT_S" "$ip" 2>&1)
    echo "$pout" | sed 's/^/   /'
    local ttl=$(echo "$pout" | sed -n 's/.*ttl=\([0-9]\+\).*/\1/p' | head -n1)
    [ -n "$ttl" ] && echo "TTL: $ttl ($(ttl_guess_os "$ttl"))"
  fi

  if have ip; then
    run "ip neigh (IPv4)" ip -4 neigh show to "$ip"
  fi
  if have arp; then
    run "arp -n" arp -n "$ip"
  fi

  # nmap ping sweep for host
  local sudo_cmd=$(maybe_sudo)
  if have "$NMAP_BIN"; then
    run "nmap -sn" $sudo_cmd "$NMAP_BIN" -n --reason -sn "$ip"
    # quick port scan for top ports
    run "nmap top-ports" $sudo_cmd "$NMAP_BIN" -n -Pn --top-ports 50 -T4 "$ip"
  fi

  # Service probes
  if have curl; then
    run "HTTP HEAD 80" curl -m "$TIMEOUT_S" -s -I "http://$ip" || true
    run "HTTPS HEAD 443" curl -m "$TIMEOUT_S" -skI "https://$ip" || true
    # Try to fetch title quickly
    local title=$(curl -m "$TIMEOUT_S" -sL "http://$ip" | sed -n 's:.*<title>\(.*\)</title>.*:\1:p' | head -n1)
    [ -n "$title" ] && echo "Title (http): $title"
  fi

  if have ssh-keyscan; then
    run "ssh-keyscan" ssh-keyscan -T "$TIMEOUT_S" -t rsa,ed25519 "$ip"
  fi

  if have nmblookup; then
    run "NetBIOS nmblookup" nmblookup -A "$ip"
  fi

  if have avahi-resolve; then
    run "mDNS (Avahi)" avahi-resolve -a "$ip"
  fi

  print_section "DHCP Leases (by ip)"
  dhcp_lookup ip "$ip"
  print_section "Watcher State (by ip)"
  state_lookup "$ip" ""
}

probe_ip6() {
  local ip="$1"
  print_section "IPv6 Target: $ip"

  local rdns=$(rdns_lookup "$ip")
  echo "rDNS: ${rdns:-<none>}"

  if have ping6; then
    ping6 -n -c 1 -W "$TIMEOUT_S" "$ip" 2>&1 | sed 's/^/   /'
  elif have ping; then
    ping -6 -n -c 1 -W "$TIMEOUT_S" "$ip" 2>&1 | sed 's/^/   /'
  fi

  if have ip; then
    run "ip neigh (IPv6)" ip -6 neigh show to "$ip"
  fi

  local sudo_cmd=$(maybe_sudo)
  if have "$NMAP_BIN"; then
    run "nmap -6 -sn" $sudo_cmd "$NMAP_BIN" -6 -n --reason -sn "$ip"
    run "nmap -6 top-ports" $sudo_cmd "$NMAP_BIN" -6 -n -Pn --top-ports 50 -T4 "$ip"
  fi

  if have curl; then
    run "HTTP HEAD 80" curl -m "$TIMEOUT_S" -s -I "http://[$ip]" || true
    run "HTTPS HEAD 443" curl -m "$TIMEOUT_S" -skI "https://[$ip]" || true
    local title=$(curl -m "$TIMEOUT_S" -sL "http://[$ip]" | sed -n 's:.*<title>\(.*\)</title>.*:\1:p' | head -n1)
    [ -n "$title" ] && echo "Title (http): $title"
  fi

  if have avahi-resolve; then
    run "mDNS (Avahi)" avahi-resolve -a "$ip"
  fi

  print_section "Watcher State (by ip6)"
  state_lookup "$ip" ""
}

probe_mac() {
  local mac="$1"
  print_section "MAC Target: $mac"

  echo "Vendor lookup:"
  mac_vendor_lookup "$mac"

  if have ip; then
    run "ip neigh -4 by mac" sh -c "ip -4 neigh | grep -i '$mac' || true"
    run "ip neigh -6 by mac" sh -c "ip -6 neigh | grep -i '$mac' || true"
  fi
  if have arp; then
    run "arp -an by mac" sh -c "arp -an | grep -i '$mac' || true"
  fi

  print_section "DHCP Leases (by mac)"
  dhcp_lookup mac "$mac"
  print_section "Watcher State (by mac)"
  state_lookup "" "$mac"
}

usage() {
  cat <<USAGE
Usage: $0 -ip4 <IPv4> | -ip6 <IPv6> | -mac <MAC>
Options via env:
  USE_SUDO=1           Use sudo for nmap
  NMAP=/path/to/nmap   nmap binary
  TIMEOUT_S=3          per-command timeout seconds
  LEASES_FILE=path     path to DHCP leases JSON
  STATE_FILE=path      watcher state JSON (default: $STATE_FILE)
USAGE
}

if [ $# -lt 2 ]; then
  usage; exit 1
fi

case "$1" in
  -ip4)
    [ -n "${2:-}" ] || { usage; exit 1; }
    probe_ip4 "$2" ;;
  -ip6)
    [ -n "${2:-}" ] || { usage; exit 1; }
    probe_ip6 "$2" ;;
  -mac)
    [ -n "${2:-}" ] || { usage; exit 1; }
    probe_mac "$2" ;;
  *)
    usage; exit 1 ;;
esac
