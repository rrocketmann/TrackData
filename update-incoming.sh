#!/bin/bash
# Pull new Polytrack 0.6.3 laps into this archive and push them.
set -euo pipefail

ARCHIVE="${POLYTRACK_LAPS_063:-$HOME/dev/trackdata}"
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/polytrack-laps-update.log"
LOCK="${XDG_STATE_HOME:-$HOME/.local/state}/polytrack-laps-update.lock"
TOPIC_063="pt360bad3248e4c33725e03b483f99b673448747fd"

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOCK"
flock -n 9 || exit 0

if [[ -f "$LOG" && "$(stat -c%s "$LOG")" -gt 2000000 ]]; then
  tail -c 400000 "$LOG" > "$LOG.trim"
  mv "$LOG.trim" "$LOG"
fi

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"
}

ensure_clone() {
  local dir="$1" repo="$2"
  if [[ ! -d "$dir/.git" ]]; then
    mkdir -p "$(dirname "$dir")"
    GIT_TERMINAL_PROMPT=0 gh repo clone "$repo" "$dir"
  fi
}

push_main() {
  local dir="$1"
  git -C "$dir" \
    -c http.postBuffer=524288000 \
    -c http.version=HTTP/1.1 \
    -c http.lowSpeedLimit=1000 \
    -c http.lowSpeedTime=120 \
    push origin HEAD:main
}

commit_new() {
  local dir="$1"
  git -C "$dir" add -- laps inbox
  if [[ -d "$dir/videos" ]]; then
    git -C "$dir" add -- videos
  fi
  if git -C "$dir" diff --cached --quiet; then
    return 0
  fi
  git -C "$dir" -c user.name=rrocketmann -c user.email=martinmalyshau@gmail.com commit -m "Add finished laps."
  if ! git -C "$dir" pull --rebase origin main; then
    git -C "$dir" rebase --abort || true
    log "rebase failed in $dir"
    return 1
  fi
  push_main "$dir"
}

collect_into() {
  local dir="$1" topic="$2" repo="$3" code=0
  ensure_clone "$dir" "$repo"
  if ! git -C "$dir" pull --rebase origin main; then
    log "pull failed in $dir"
    commit_new "$dir" || return 1
    git -C "$dir" pull --rebase origin main
  fi
  set +e
  (
    cd "$dir"
    TOPIC="$topic" VIDEO_STORE=git python3 collect.py
  ) >>"$LOG" 2>&1
  code=$?
  set -e
  if [[ "$code" -ne 0 && "$code" -ne 1 ]]; then
    log "collect exit $code in $dir"
    return "$code"
  fi
  commit_new "$dir"
}

log "start"
collect_into "$ARCHIVE" "$TOPIC_063" "rrocketmann/polytrack-laps-063" || log "collect failed"
log "done laps=$(find "$ARCHIVE/laps" -name '*.json.gz' | wc -l) videos=$(find "$ARCHIVE/videos" -type f ! -name .gitkeep | wc -l)"
