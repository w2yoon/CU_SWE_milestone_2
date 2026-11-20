#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- B.2 messages, paging, replies, pins, search ---
mkws
mkch "$OT" "c$S"; CH=$CH; req POST /api/channels/$CH/join "$memT"; CHPUB=$CH
req POST /api/channels/$CH/messages "$OT" '{"body":"   "}'; expect B2.1 "empty body -> 400" 400
req POST /api/channels/$CH/messages "" '{"body":"x"}'; expect B2.2 "post no token -> 401" 401
req POST /api/channels/999999999/messages "$OT" '{"body":"x"}'; expect B2.3 "post unknown channel -> 404" 404
req POST /api/channels/$CH/messages "$OT" '{"body":"m1"}'; expect B2.4 "post 201 MessageObj fields" 201 "all(k in d['message'] for k in ('id','channel_id','author_id','author','body','parent_id','reply_count','created_at','edited_at','files','reactions','mentions')) and d['message']['reply_count']==0 and d['message']['parent_id'] is None and d['message']['created_at'].endswith('Z')"
M1=$(pyget "$OUT" 'd["message"]["id"]')
for i in 2 3 4 5; do post "$OT" $CH "m$i"; eval "M$i=$MID"; done
req GET "/api/channels/$CH/messages?limit=2" "$OT"; expect B2.5 "page1 newest-first + cursor" 200 "[m['id'] for m in d['messages']]==[$M5,$M4] and isinstance(d['next_cursor'],str)"
CUR=$(python3 -c 'import sys,json,urllib.parse;print(urllib.parse.quote(json.loads(sys.argv[1])["next_cursor"]))' "$OUT")
post "$OT" $CH "m6"; M6=$MID
req GET "/api/channels/$CH/messages?limit=2&cursor=$CUR" "$OT" "" "http://127.0.0.1:8001"; expect B2.6 "page2 on another node, stable under new msg" 200 "[m['id'] for m in d['messages']]==[$M3,$M2] and isinstance(d['next_cursor'],str)"
CUR=$(python3 -c 'import sys,json,urllib.parse;print(urllib.parse.quote(json.loads(sys.argv[1])["next_cursor"]))' "$OUT")
req GET "/api/channels/$CH/messages?limit=2&cursor=$CUR" "$OT" "" "http://127.0.0.1:8002"; expect B2.7 "last page next_cursor null" 200 "[m['id'] for m in d['messages']]==[$M1] and d['next_cursor'] is None"
req GET "/api/channels/$CH/messages" "$OT"; expect B2.8 "default limit returns all newest-first" 200 "[m['id'] for m in d['messages']][:2]==[$M6,$M5] and len(d['messages'])==6 and d['next_cursor'] is None"
req GET "/api/channels/$CH/messages?limit=0" "$OT"; expect B2.9 "limit=0 -> 400" 400
req GET "/api/channels/$CH/messages?limit=201" "$OT"; expect B2.10 "limit=201 -> 400" 400
req GET "/api/channels/$CH/messages?limit=abc" "$OT"; expect B2.11 "limit=abc -> 400" 400
req GET "/api/channels/$CH/messages?cursor=@@garbage@@" "$OT"; expect B2.12 "malformed cursor -> 400" 400
req GET "/api/channels/$CH/messages?limit=200" "$OT"; expect B2.13 "limit=200 ok" 200
req GET "/api/channels/$CH/messages" ""; expect B2.14 "history no token -> 401" 401
post "$OT" $CH "r1" $M1; R1=$MID; expect B2.15 "reply 201 with parent_id" 201 "d['message']['parent_id']==$M1"
post "$OT" $CH "r2" $M1; R2=$MID
req GET "/api/messages/$M1/replies" "$OT"; expect B2.16 "replies oldest-first" 200 "[m['id'] for m in d['replies']]==[$R1,$R2] and d['next_cursor'] is None"
req GET "/api/messages/$M1/replies?limit=1" "$OT"; expect B2.17 "replies paged" 200 "len(d['replies'])==1 and isinstance(d['next_cursor'],str)"
req GET "/api/channels/$CH/messages?limit=200" "$OT"; expect B2.18 "parent reply_count==2" 200 "[m for m in d['messages'] if m['id']==$M1][0]['reply_count']==2"
req POST /api/messages/$M2/pin "$OT"; expect B2.19 "pin" 200 "d['pin']['message_id']==$M2 and 'pinned_by' in d['pin'] and 'pinned_at' in d['pin']"
req POST /api/messages/$M2/pin "$OT"; expect B2.20 "pin idempotent" 200
req POST /api/messages/$M3/pin "$OT"
req GET /api/channels/$CH/pins "$memT"; expect B2.21 "pins newest-first" 200 "[p['message']['id'] for p in d['pins']]==[$M3,$M2] and all(k in d['pins'][0] for k in ('message','pinned_by','pinned_at')) and 'next_cursor' in d"
req GET "/api/channels/$CH/pins?limit=1" "$OT"; expect B2.22 "pins paged" 200 "len(d['pins'])==1 and isinstance(d['next_cursor'],str)"
req DELETE /api/messages/$M2/pin "$OT"; expect B2.23 "unpin" 200 "d['unpinned']==True"
mkch "$OT" "pv$S" private; PRV=$CH; CH=$CHPUB
req GET /api/channels/$PRV/pins "$memT"; expect B2.24 "private pins non-member -> 403" 403
post "$OT" $CHPUB "zebra$S unique"
req GET "/api/search?q=zebra$S" "$memT"; expect B2.25 "search finds message" 200 "len(d['results'])==1 and 'next_cursor' in d"
req GET "/api/search?q=zebra$S&workspace_id=$WID" "$memT"; expect B2.26 "search workspace_id filter" 200 "len(d['results'])==1"
req GET "/api/search?q=zebra$S&workspace_id=999999" "$memT"; expect B2.27 "search other workspace empty" 200 "len(d['results'])==0"
post "$OT" $PRV "quokka$S secret"
req GET "/api/search?q=quokka$S" "$memT"; expect B2.28 "search hides private non-member msgs" 200 "len(d['results'])==0"
req GET "/api/search?q=quokka$S" "$OT"; expect B2.29 "search shows private to member" 200 "len(d['results'])==1"

summary
