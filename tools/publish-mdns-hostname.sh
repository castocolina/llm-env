#!/usr/bin/env bash
# publish-mdns-hostname.sh — keep an mDNS hostname alias (and the
# _http._tcp service record used for zeroconf discovery) pointed at this
# host's current primary IPv4 address for as long as this process runs.
#
# `avahi-publish -a -R <name> <ip>` takes the IP as a literal argument: it
# never re-resolves it, so a one-shot invocation goes stale on any DHCP
# lease renewal or network switch. This wrapper re-checks the current
# address on a short interval and restarts the -a -R publish only when it
# actually changes, so the record it's the sole owner of never goes stale
# for long. It also runs the -s service-record publish once in the
# background: that one doesn't embed an address (clients resolve it via
# avahi-daemon's own dynamically-tracked hostname), so it needs no
# refreshing.
set -uo pipefail

name="${1:?usage: publish-mdns-hostname.sh <hostname> <service-name> <port>}"
service_name="${2:?usage: publish-mdns-hostname.sh <hostname> <service-name> <port>}"
port="${3:?usage: publish-mdns-hostname.sh <hostname> <service-name> <port>}"
poll_seconds="${LLM_ENV_MDNS_POLL_SECONDS:-15}"

hostname_pid=""
service_pid=""
last_ip=""

cleanup() {
    [ -z "$hostname_pid" ] || kill "$hostname_pid" 2>/dev/null
    [ -z "$service_pid" ] || kill "$service_pid" 2>/dev/null
    exit 0
}
trap cleanup TERM INT

avahi-publish -s "$service_name" _http._tcp "$port" &
service_pid=$!

while true; do
    ip="$(ip -4 -json addr show scope global 2>/dev/null \
          | jq -r '[.[].addr_info[].local] | first // empty')"
    if [ -n "$ip" ] && [ "$ip" != "$last_ip" ]; then
        [ -z "$hostname_pid" ] || kill "$hostname_pid" 2>/dev/null
        avahi-publish -a -R "$name" "$ip" &
        hostname_pid=$!
        last_ip="$ip"
        printf 'publish-mdns-hostname: %s -> %s\n' "$name" "$ip"
    fi
    sleep "$poll_seconds" &
    wait $!
done
