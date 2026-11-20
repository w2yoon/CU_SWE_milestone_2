#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- B.1 channel management ---
mkws
T251=$(printf 'x%.0s' $(seq 251)); T250=$(printf 'y%.0s' $(seq 250)); N33=$(printf 'a%.0s' $(seq 33))
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"c$S\"}"; expect B1.1 "create channel: topic empty, flags" 201 "d['channel']['topic']=='' and d['channel']['is_private']==False and d['channel']['is_archived']==False and d['channel']['is_dm']==False and all(k in d['channel'] for k in ('id','workspace_id','name'))"
CH=$(pyget "$OUT" 'd["channel"]["id"]')
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"c$S\"}"; expect B1.2 "duplicate name -> 409" 409
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"$N33\"}"; expect B1.3 "name >32 chars -> 400" 400
req POST /api/workspaces/$W/channels "$OT" '{"name":"Bad Name"}'; expect B1.4 "non-kebab name -> 400" 400
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"t$S\",\"topic\":\"$T251\"}"; expect B1.5 "topic >250 on create -> 400" 400
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"t2$S\",\"topic\":\"hello\"}"; expect B1.6 "topic accepted on create" 201 "d['channel']['topic']=='hello'"
req POST /api/workspaces/$W/channels "" "{\"name\":\"nt$S\"}"; expect B1.7 "create channel no token" 401
req POST /api/channels/$CH/join "$memT"
req PATCH /api/channels/$CH "$OT" '{"topic":"new topic"}'; expect B1.8 "owner sets topic" 200 "d['channel']['topic']=='new topic'"
req PATCH /api/channels/$CH "$memT" "{\"topic\":\"$T250\"}"; expect B1.9 "channel member sets 250-char topic" 200 "len(d['channel']['topic'])==250"
req PATCH /api/channels/$CH "$OT" "{\"topic\":\"$T251\"}"; expect B1.10 "topic 251 -> 400" 400
req PATCH /api/channels/$CH "$outT" '{"topic":"hax"}'; expect B1.11 "non-member cannot set topic (403)" 403
req PATCH /api/channels/999999999 "$OT" '{"topic":"x"}'; expect B1.12 "patch unknown channel 404" 404
req GET /api/workspaces/$W "$OT"; expect B1.13 "ws detail: channels+read_state" 200 "'read_state' in d and any(c['name']=='c$S' for c in d['channels']) and d['workspace']['slug']=='$W'"
mkch "$OT" "p$S" private; PRV=$CH
req GET /api/workspaces/$W "$memT"; expect B1.14 "private hidden from non-member" 200 "not any(c['name']=='p$S' for c in d['channels'])"
req GET /api/workspaces/$W "$OT"; expect B1.15 "private visible to creator" 200 "any(c['name']=='p$S' for c in d['channels'])"
req GET /api/channels/$GEN/members "$OT"; expect B1.16 "channel members UserObj" 200 "any(u['id']==$OID and all(k in u for k in ('username','display_name','timezone','avatar_url','status_text','status_emoji')) for u in d['members'])"
req POST /api/channels/$GEN/leave "$memT"; expect B1.17 "leave" 200 "d['channel']['id']==$GEN"
req POST /api/channels/$GEN/leave "$memT"; expect B1.18 "leave idempotent" 200
req GET /api/channels/$GEN/members "$OT"; expect B1.19 "left user not in members" 200 "not any(u['id']==$memI for u in d['members'])"
mkch "$OT" "arc$S"; ARC=$CH
req POST /api/channels/$ARC/join "$memT"
req POST /api/channels/$ARC/archive "$memT"; expect B1.20 "non-owner archive (403)" 403
req POST /api/channels/$ARC/archive "$OT"; expect B1.21 "owner archives" 200 "d['channel']['is_archived']==True"
req POST /api/channels/$ARC/messages "$OT" '{"body":"hi"}'; expect B1.22 "write to archived -> 423" 423
req GET /api/workspaces/$W "$OT"; expect B1.23 "archived hidden by default" 200 "not any(c['name']=='arc$S' for c in d['channels'])"
req GET "/api/workspaces/$W?include_archived=1" "$OT"; expect B1.24 "include_archived=1 shows it" 200 "any(c['name']=='arc$S' for c in d['channels'])"
req POST /api/channels/$ARC/unarchive "$OT"; expect B1.25 "owner unarchives" 200 "d['channel']['is_archived']==False"
req POST /api/channels/$ARC/messages "$OT" '{"body":"hi again"}'; expect B1.26 "write after unarchive" 201

summary
