#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- B.3 mentions and read state ---
mkws
mkch "$OT" "c$S"; req POST /api/channels/$CH/join "$memT"; req POST /api/channels/$CH/join "$admT"
post "$OT" $CH "hey @mem$S"; req GET "/api/channels/$CH/messages?limit=1" "$OT"; expect B3.1 "@username -> id" 200 "d['messages'][0]['mentions']==[$memI]"
post "$OT" $CH "@mem$S and @mem$S again"; req GET "/api/channels/$CH/messages?limit=1" "$OT"; expect B3.2 "deduped" 200 "d['messages'][0]['mentions']==[$memI]"
post "$OT" $CH "mail foo@mem$S and a@b.com"; req GET "/api/channels/$CH/messages?limit=1" "$OT"; expect B3.3 "in-word/email no match" 200 "d['messages'][0]['mentions']==[]"
post "$OT" $CH "me @o$S"; req GET "/api/channels/$CH/messages?limit=1" "$OT"; expect B3.4 "no self mention" 200 "d['messages'][0]['mentions']==[]"
post "$OT" $CH "ghost @out$S"; req GET "/api/channels/$CH/messages?limit=1" "$OT"; expect B3.5 "non-workspace-member excluded" 200 "d['messages'][0]['mentions']==[]"
post "$OT" $CH "@channel everyone"; req GET "/api/channels/$CH/messages?limit=1" "$OT"; expect B3.6 "@channel = members except author" 200 "sorted(d['messages'][0]['mentions'])==sorted([$memI,$admI])"
req POST /api/channels/$CH/messages "$memT" "{\"body\":\"@channel hi @mem$S\"}"; expect B3.7 "post response carries mentions (no self)" 201 "sorted(d['message']['mentions'])==sorted([$OID,$admI])"
req GET /api/channels/$CH/read "$memT"; expect B3.8 "read_state shape" 200 "all(k in d['read_state'] for k in ('channel_id','last_read_event_id','unread_count','mention_count')) and d['read_state']['channel_id']==$CH"
req GET /api/channels/$CH/read "$memT"; expect B3.9 "member unread/mention counts" 200 "d['read_state']['unread_count']>=6 and d['read_state']['mention_count']>=3"
req GET /api/channels/$CH/read "$OT"; expect B3.10 "owner: self-mentions not counted" 200 "d['read_state']['mention_count']==1"
req POST /api/channels/$CH/read "$memT" '{"last_read_event_id":4}'; expect B3.11 "advance cursor" 200 "d['read_state']['last_read_event_id']==4"
req POST /api/channels/$CH/read "$memT" '{"last_read_event_id":2}'; expect B3.12 "never moves backwards" 200 "d['read_state']['last_read_event_id']==4"
req GET /api/channels/$CH/read "$memT"; expect B3.13 "unread decreased after read" 200 "d['read_state']['unread_count']<6"
req GET /api/workspaces/$W "$memT"; expect B3.14 "workspace detail embeds read_state" 200 "'read_state' in d and d['read_state'] is not None"
req GET /api/channels/$CH/read ""; expect B3.15 "read no token -> 401" 401

summary
