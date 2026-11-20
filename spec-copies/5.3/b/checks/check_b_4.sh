#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- B.4 event ordering over WebSocket / cluster ---
EV=$(timeout 25 python3 "$(dirname "$0")/events.py" 2>&1)
while read -r l; do
  case "$l" in PASS*) PASS=$((PASS+1));; FAIL*) FAIL=$((FAIL+1));; *) continue;; esac
  [ "$FEEDBACK" = verbose ] && echo "$l"
done <<<"$EV"
echo "$EV" | grep -q '^EVENTS: ' || bad B4.0 "events.py did not finish" "${EV: -300}"
summary
