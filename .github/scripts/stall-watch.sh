#!/usr/bin/env bash
# Runs a CI step's command and stops it only when it stops making progress. It never limits total
# time: a slow runner makes a step slow, not wrong. On 2026-10-16 a 60-minute job limit killed a
# Fedora headless shard of PR #63 five seconds after its last PASS line, because a slow Fedora
# mirror had made the test image take 29 minutes to build. stall-watch.ps1 is its Windows twin.
#
#   stall-watch.sh [--log FILE] [--name NAME] -- COMMAND [ARGS...]
#
# Progress is either of two things: a new line of output, or the runner receiving at least
# STALL_RX_MIB MiB (default 4) since the last line or the last such crossing, read from
# /proc/net/dev. The second is there because a download prints nothing until it finishes: in that
# same shard, dnf printed no line for 15.3 minutes while it fetched Fedora's 38 MiB of repository
# metadata at 42 KiB/s, about 12 times the 4 MiB in 20 minutes (3.4 KiB/s) taken as progress. With
# neither for STALL_MIN minutes (default 20), the step has stalled.
#
# The command's stdout and stderr go to this step's output and to FILE (when given; a temporary
# file otherwise). On a stall it prints a hang record (what was running, how long, when the last
# line came, the last lines, the process tree and the containers), stops the command's process
# group and its containers, and exits 124. Otherwise it exits with the command's own status.
# Either way it reports how long the step took and how much the runner received, as a notice, so
# slow trends show without failing anything. For this script's own tests, STALL_S overrides
# STALL_MIN in seconds, STALL_POLL_S sets the poll interval, STALL_DRAIN_S the drain's quiet
# window, and STALL_NET_DEV the file read in place of /proc/net/dev.
set -u

log=""
name="step"
while [ $# -gt 0 ]; do
  case $1 in
    --log) log=$2; shift 2 ;;
    --name) name=$2; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
if [ $# -eq 0 ]; then
  echo "stall-watch.sh: no command given" >&2
  exit 2
fi

stall_s=${STALL_S:-$(( ${STALL_MIN:-20} * 60 ))}
poll_s=${STALL_POLL_S:-5}
drain_s=${STALL_DRAIN_S:-30}
rx_mib=${STALL_RX_MIB:-4}
rx_step=$(( rx_mib * 1048576 ))
net_dev=${STALL_NET_DEV:-/proc/net/dev}
if [ -z "$log" ]; then log=$(mktemp); fi
mkdir -p "$(dirname "$log")"
: > "$log"
fifo=$(mktemp -u)
mkfifo "$fifo"
trap 'rm -f "$fifo"' EXIT

minutes() { printf '%dm %02ds' $(( $1 / 60 )) $(( $1 % 60 )); }
mib() { printf '%d.%d MiB' $(( $1 / 1048576 )) $(( $1 % 1048576 * 10 / 1048576 )); }
# kill -0 also succeeds on a zombie, so read the state after the last ')' of /proc/PID/stat
alive() {
  local stat
  stat=$(cat "/proc/$1/stat" 2> /dev/null) || return 1
  stat=${stat##*) }
  case ${stat%% *} in Z|X) return 1 ;; esac
}
# Bytes received on every interface but loopback
rx_bytes() {
  awk 'NR > 2 { sub(/^ */, ""); split($0, f, /:[ ]*/); if (f[1] != "lo") { split(f[2], v, / +/); s += v[1] } }
       END { printf "%.0f\n", s }' "$net_dev" 2> /dev/null || echo 0
}
# Counts only increases: an interface that goes away (a container's, when it ends) takes its
# bytes out of the sum, which is not data received
rx_prev=$(rx_bytes)
rx_total=0
rx_since=0
rx_poll() {
  local now_rx delta
  now_rx=$(rx_bytes)
  delta=$(( now_rx - rx_prev ))
  rx_prev=$now_rx
  if [ "$delta" -gt 0 ]; then rx_total=$(( rx_total + delta )); rx_since=$(( rx_since + delta )); fi
}
# The processes in process group $1, or every process when $1 is empty: with ps, or from /proc
# where there is no ps (a Debian container)
procs() {
  local group=${1:-} p stat fields
  if command -v ps > /dev/null; then
    if [ -n "$group" ]; then ps -o pid,ppid,etime,time,stat,args -g "$group" 2> /dev/null
    else ps -eo pid,ppid,pgid,etime,time,stat,args --forest; fi
    return
  fi
  for p in /proc/[0-9]*; do
    stat=$(cat "$p/stat" 2> /dev/null) || continue
    read -r -a fields <<< "${stat##*) }"   # state, ppid, pgrp, ...
    if [ -z "$group" ] || [ "${fields[2]}" = "$group" ]; then
      printf '%s ppid %s pgid %s %s: %s\n' "${p#/proc/}" "${fields[1]}" "${fields[2]}" "${fields[0]}" \
        "$(tr '\0' ' ' < "$p/cmdline" 2> /dev/null)"
    fi
  done
}

start=$(date +%s)
tee "$log" < "$fifo" &
reader=$!
# Its own process group, so a stall stops everything the command started
setsid "$@" > "$fifo" 2>&1 &
cmd=$!

progress=$start
stalled=0
while alive "$cmd"; do
  sleep "$poll_s"
  now=$(date +%s)
  rx_poll
  wrote=$(stat -c %Y "$log")
  if [ "$wrote" -gt "$progress" ]; then
    progress=$wrote
    rx_since=0
  elif [ "$rx_since" -ge "$rx_step" ]; then
    progress=$now
    rx_since=0
  fi
  quiet=$(( now - progress ))
  if [ "$quiet" -ge "$stall_s" ] && alive "$cmd"; then
    stalled=1
    echo "::error title=${name} stalled::no output and under ${rx_mib} MiB received for $(minutes "$quiet"), after $(minutes $(( now - start ))); stopped it. The hang record is in the step's log."
    echo "===== HANG RECORD: ${name} ====="
    echo "running:     $*"
    echo "elapsed:     $(minutes $(( now - start )))"
    echo "last output: $(minutes $(( now - wrote ))) ago, at $(date -u -d "@$wrote" +%H:%M:%SZ)"
    echo "received:    $(mib "$rx_since") since the last progress, $(mib "$rx_total") in all"
    echo "----- last 40 lines -----"
    tail -n 40 "$log"
    echo "----- process tree -----"
    procs
    echo "----- containers -----"
    docker ps 2> /dev/null || echo "(no docker here)"
    echo "===== END HANG RECORD ====="
    kill -TERM -- "-$cmd" 2> /dev/null
    sleep 10
    kill -KILL -- "-$cmd" 2> /dev/null
    docker ps -q 2> /dev/null | xargs -r docker kill > /dev/null 2>&1 || true
    break
  fi
done
wait "$cmd"
status=$?

# Anything the command left running would hold the pipe open and keep the step waiting. TERM it;
# what is still there with no new output for STALL_DRAIN_S seconds is killed, and named
kill -TERM -- "-$cmd" 2> /dev/null
drained=$(date +%s)
while alive "$reader"; do
  sleep 1
  wrote=$(stat -c %Y "$log")
  if [ "$wrote" -gt "$drained" ]; then drained=$wrote; fi
  if [ $(( $(date +%s) - drained )) -ge "$drain_s" ]; then
    echo "::notice title=${name} leftovers::the command ended, but these kept its output open for ${drain_s}s with nothing new; killed them"
    procs "$cmd"
    kill -KILL -- "-$cmd" 2> /dev/null
    kill "$reader" 2> /dev/null
    break
  fi
done
wait "$reader" 2> /dev/null
rx_poll

took=$(minutes $(( $(date +%s) - start )))
if [ "$stalled" = 1 ]; then
  echo "::notice title=${name} duration::${name} stalled and was stopped after ${took}; the runner received $(mib "$rx_total")"
  exit 124
fi
echo "::notice title=${name} duration::${name} took ${took} (exit ${status}); the runner received $(mib "$rx_total")"
exit "$status"
