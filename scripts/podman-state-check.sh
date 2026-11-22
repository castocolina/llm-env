#!/usr/bin/env bash
# podman-state-check.sh — report container runtime and DNS state without changing it.
set -euo pipefail

result=OK
record() {
    local check="$1" state="$2" message="${3:-}"
    printf 'podman-state: %s %s' "$check" "$state"
    [[ -z "$message" ]] || printf ' (%s)' "$message"
    printf '\n'
    if [[ "$state" == FAIL ]]; then result=FAIL
    elif [[ "$state" == UNKNOWN && "$result" == OK ]]; then result=UNKNOWN
    fi
}

crun_rows=""
podman_rows=""
if ! crun_rows="$(crun --root "/run/user/$(id -u)/crun" list 2>/dev/null)" || ! podman_rows="$(podman ps -a --no-trunc --format '{{.ID}} {{.State}}' 2>/dev/null)"; then
    record STATE UNKNOWN 'crun or podman unavailable'
else
    mismatched_ids=()
    while read -r id _ state _; do
        [[ -n "${id:-}" && "$id" != ID && "$id" != buildah-* && "${state:-}" == running ]] || continue
        reported="$(awk -v id="$id" '$1 == id {print $2; exit}' <<< "$podman_rows")"
        if [[ -n "$reported" && "$reported" != running ]]; then mismatched_ids+=("$id"); fi
    done <<< "$crun_rows"
    if (( ${#mismatched_ids[@]} )); then
        record STATE FAIL 'crun reports a running container that podman does not'
        podman ps -a --no-trunc --format '{{.ID}} {{.Names}}' 2>/dev/null | while read -r id name; do
            for mismatch_id in "${mismatched_ids[@]}"; do
                if [[ "$id" == "$mismatch_id" ]]; then
                    printf 'podman-state: container %s %s\n' "$id" "$name"
                fi
            done
        done
    else
        record STATE OK
    fi
fi

if ! podman container exists omniroute >/dev/null 2>&1 || [[ "$(podman inspect --format '{{.State.Status}}' omniroute 2>/dev/null || true)" != running ]]; then
    record DNS UNKNOWN 'omniroute absent or not running'
else
    image="$(podman inspect --format '{{.ImageName}}' omniroute 2>/dev/null || true)"
    host="${image%%/*}"
    if [[ -z "$host" || "$host" == "$image" ]] || ! getent ahosts "$host" >/dev/null 2>&1; then
        record DNS UNKNOWN 'host image registry lookup failed'
    else
        dns_code='const fs=require("fs"),dgram=require("dgram");const host=process.argv[1];const ns=(fs.readFileSync(process.env.PODMAN_STATE_RESOLV||"/etc/resolv.conf","utf8").match(/^nameserver\s+(\S+)/m)||[])[1];const end=x=>{console.log(x);process.exit(0)};if(!ns)end("NOTUDP");const s=dgram.createSocket("udp4");const id=Math.floor(Math.random()*65536);const labels=host.split(".").map(x=>Buffer.concat([Buffer.from([Buffer.byteLength(x)]),Buffer.from(x)]));const q=Buffer.concat([Buffer.from([id>>8,id&255,1,0,0,1,0,0,0,0,0,0]),...labels,Buffer.from([0,0,1,0,1])]);s.on("message",m=>{const r=m[3]&15,a=m.readUInt16BE(6);end(r===0&&a>=1?"OK":r===3?"NXDOMAIN":r===2?"SERVFAIL":"OTHER")});s.on("error",()=>end("SERVFAIL"));s.send(q,Number(process.env.PODMAN_STATE_DNS_PORT||53),ns);setTimeout(()=>end("NOREPLY"),3000);'
        if output="$(timeout 10 podman exec omniroute node -e "$dns_code" "$host" 2>/dev/null)"; then
            case "$output" in
                OK) record DNS OK ;;
                NOREPLY|SERVFAIL) record DNS FAIL 'container resolver does not answer; restart the DNS helper (aardvark-dns) after checking bridges' ;;
                *) record DNS UNKNOWN 'container resolver result unavailable' ;;
            esac
        else
            record DNS UNKNOWN 'container DNS probe failed'
        fi
    fi
fi

printf 'podman-state: SUMMARY %s\n' "$result"
[[ "$result" != FAIL ]]
