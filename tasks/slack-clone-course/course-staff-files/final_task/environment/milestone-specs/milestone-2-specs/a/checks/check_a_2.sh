#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- A.2 roles, ownership transfer, role-based channel permissions ---
mkws
req GET /api/workspaces/$W/members "$OT"; expect A4.0 "setup: admin+guest assigned" 200 "{m['user_id']:m['role'] for m in d['members']}[$admI]=='admin'"
req PATCH /api/workspaces/$W/members/$memI "$OT" '{"role":"admin"}'; expect A4.1 "owner promotes member->admin" 200 "d['member']['role']=='admin' and d['member']['user_id']==$memI"
req PATCH /api/workspaces/$W/members/$memI "$admT" '{"role":"guest"}'; expect A4.2 "admin demotes admin? -> 403 (admin cannot modify admin)" 403
req PATCH /api/workspaces/$W/members/$memI "$OT" '{"role":"member"}'; expect A4.3 "owner demotes admin->member" 200 "d['member']['role']=='member'"
req PATCH /api/workspaces/$W/members/$memI "$admT" '{"role":"guest"}'; expect A4.4 "admin sets member->guest" 200 "d['member']['role']=='guest'"
req PATCH /api/workspaces/$W/members/$memI "$admT" '{"role":"member"}'; expect A4.5 "admin sets guest->member" 200 "d['member']['role']=='member'"
req PATCH /api/workspaces/$W/members/$memI "$OT" '{"role":"owner"}'; expect A4.6 "role=owner via PATCH -> 400" 400
req PATCH /api/workspaces/$W/members/$OID "$admT" '{"role":"member"}'; expect A4.7 "admin cannot change owner" 403
req PATCH /api/workspaces/$W/members/$OID "$OT" '{"role":"member"}'; expect A4.8 "owner role immutable via PATCH" 403
req PATCH /api/workspaces/$W/members/$gstI "$memT" '{"role":"member"}'; expect A4.9 "member cannot PATCH roles" 403
req PATCH /api/workspaces/$W/members/$gstI "" '{"role":"member"}'; expect A4.10 "PATCH role no token" 401
req PATCH /api/workspaces/$W/members/$memI "$OT" '{"role":"superuser"}'; expect A4.11 "invalid role value" 400
req PATCH /api/workspaces/$W/members/abc "$OT" '{"role":"member"}'; expect A4.12 "non-numeric user_id -> 404" 404
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"pub$S\"}"; expect A5.0 "owner creates public" 201
PUB=$(pyget "$OUT" 'd["channel"]["id"]')
mkch "$OT" "prv$S" private; PRV=$CH
req POST /api/channels/$PUB/join "$gstT"; expect A5.1 "guest cannot join public (403)" 403
req POST /api/workspaces/$W/channels "$gstT" "{\"name\":\"g$S\"}"; expect A5.2 "guest cannot create channel (403)" 403
req POST /api/workspaces/$W/channels "$memT" "{\"name\":\"m$S\",\"is_private\":true}"; expect A5.3 "member cannot create private (403)" 403
req POST /api/workspaces/$W/channels "$memT" "{\"name\":\"mp$S\"}"; expect A5.4 "member creates public (201)" 201
req POST /api/workspaces/$W/channels "$admT" "{\"name\":\"ap$S\",\"is_private\":true}"; expect A5.5 "admin creates private (201)" 201
req POST /api/channels/$PRV/join "$memT"; expect A5.6 "private join without invite (403)" 403
req POST /api/workspaces/$W/channels "$outT" "{\"name\":\"o$S\"}"; expect A5.7 "non-member cannot create channel (403)" 403
req POST /api/workspaces/$W/transfer_ownership "$admT" "{\"new_owner_id\":$admI}"; expect A8.1 "non-owner cannot transfer (403)" 403
req POST /api/workspaces/$W/transfer_ownership "$OT" "{\"new_owner_id\":$memI}"; expect A8.2 "target must be admin (400)" 400
req POST /api/workspaces/$W/transfer_ownership "$OT" "{\"new_owner_id\":99999999}"; expect A8.3 "unknown target (400 or 404)" 400
req POST /api/workspaces/$W/transfer_ownership "$OT" "{\"new_owner_id\":$admI}"; expect A8.4 "transfer ok" 200
req GET /api/workspaces/$W/members "$admT"; expect A8.5 "roles swapped" 200 "{m['user_id']:m['role'] for m in d['members']}[$admI]=='owner' and {m['user_id']:m['role'] for m in d['members']}[$OID]=='admin'"
req GET /api/workspaces "$admT"; expect A8.6 "workspace owner_id updated" 200 "[w for w in d['workspaces'] if w['slug']=='$W'][0]['owner_id']==$admI"

summary
