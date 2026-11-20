#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- A.3 invitations and join mode ---
mkws
req POST /api/workspaces/$W/channels "$OT" "{\"name\":\"pub$S\"}"; PUB=$(pyget "$OUT" 'd["channel"]["id"]')
req POST /api/workspaces/$W/invitations "$memT" '{}'; expect A6.1 "member cannot invite (403)" 403
req POST /api/workspaces/$W/invitations "" '{}'; expect A6.1b "invite no token (401)" 401
req POST /api/workspaces/$W/invitations "$admT" '{}'; expect A6.2 "admin creates invite (201, code)" 201 "len(d['invitation']['code'])>0"
C=$(pyget "$OUT" 'd["invitation"]["code"]')
req GET /api/workspaces/$W/invitations "$OT"; expect A6.3 "owner lists invitations" 200 "any(i['code']=='$C' for i in d['invitations'])"
req GET /api/workspaces/$W/invitations "$memT"; expect A6.3b "member cannot list invitations (403)" 403
req POST /api/invitations/$C/accept "$outT"; expect A6.4 "accept invite" 200
req GET /api/workspaces/$W/members "$OT"; expect A6.4b "accepter is member" 200 "{m['user_id']:m['role'] for m in d['members']}[$outI]=='member'"
reg "late$S"; LT=$TOK
req POST /api/invitations/$C/accept "$LT"; expect A6.5 "exhausted (max_uses default 1) -> 404" 404
req POST /api/invitations/nosuchcode/accept "$LT"; expect A6.6 "unknown code -> 404" 404
req POST /api/workspaces/$W/invitations "$OT" "{\"invited_user_id\":$memI}"; TC=$(pyget "$OUT" 'd["invitation"]["code"]')
req POST /api/invitations/$TC/accept "$LT"; expect A6.7 "wrong user on targeted invite -> 403" 403
req POST /api/workspaces/$W/invitations "$OT" '{"max_uses":2}'; MC=$(pyget "$OUT" 'd["invitation"]["code"]')
reg "m1$S"; M1=$TOK; reg "m2$S"; M2=$TOK; reg "m3$S"; M3=$TOK
req POST /api/invitations/$MC/accept "$M1"; expect A6.8 "max_uses=2 first" 200
req POST /api/invitations/$MC/accept "$M2"; expect A6.9 "max_uses=2 second" 200
req POST /api/invitations/$MC/accept "$M3"; expect A6.10 "max_uses=2 third -> 404" 404
req POST /api/workspaces/$W/invitations "$OT" '{"expires_in_seconds":1}'; EC=$(pyget "$OUT" 'd["invitation"]["code"]')
sleep 2
req POST /api/invitations/$EC/accept "$M3"; expect A6.11 "expired -> 404" 404
req POST /api/workspaces/$W/invitations "$OT" '{}'; RC=$(pyget "$OUT" 'd["invitation"]["code"]')
req DELETE /api/workspaces/$W/invitations/$RC "$memT"; expect A6.12 "member cannot revoke (403)" 403
req DELETE /api/workspaces/$W/invitations/$RC "$OT"; expect A6.13 "revoke -> 200 deleted" 200 "d['deleted']==True"
req POST /api/invitations/$RC/accept "$M3"; expect A6.14 "revoked -> 404" 404
req POST /api/workspaces/$W/invitations "$OT" "{\"invited_username\":\"tu$S\"}"; expect A6.15 "invited_username accepted at creation" 201
UC=$(pyget "$OUT" 'd["invitation"]["code"]'); reg "tu$S"; TUT=$TOK
req POST /api/invitations/$UC/accept "$LT"; expect A6.15b "wrong user on invited_username invite -> 403" 403
req POST /api/invitations/$UC/accept "$TUT"; expect A6.15c "named user accepts" 200
req POST /api/workspaces/$W/invitations "$OT" "{\"invited_user_id\":$memI,\"max_uses\":5}"; expect A6.16 "targeted invite is single-use (max_uses forced to 1)" 201 "d['invitation']['max_uses']==1"
req POST /api/workspaces/$W/invitations "$OT" '{"max_uses":0}'; expect A6.20 "max_uses=0 -> 400" 400
req POST /api/workspaces/$W/invitations "$OT" '{"max_uses":"abc"}'; expect A6.21 "max_uses non-integer -> 400" 400
req DELETE /api/workspaces/$W/invitations/nosuchcode "$OT"; expect A6.18 "revoke unknown code -> 404" 404
req GET /api/workspaces/$W/invitations "$OT"; expect A6.17 "list excludes exhausted/expired/revoked" 200 "not any(i['code'] in ('$C','$EC','$RC','$MC','$UC') for i in d['invitations'])"
req POST /api/workspaces/$W/invitations "$OT" '{"max_uses":1}'; CC=$(pyget "$OUT" 'd["invitation"]["code"]')
for i in 1 2 3 4 5 6; do reg "cc$i$S"; eval "CT$i=$TOK"; done
rm -f /tmp/_cc.$$.*
for i in 1 2 3 4 5 6; do eval "t=\$CT$i"; curl -s -m 3 -o /dev/null -w '%{http_code}\n' -X POST $BASE/api/invitations/$CC/accept -H "Authorization: Bearer $t" -H 'Content-Type: application/json' -d '{}' > /tmp/_cc.$$.$i & done; wait
N200=$(cat /tmp/_cc.$$.* | grep -c '^200$'); N404=$(cat /tmp/_cc.$$.* | grep -c '^404$'); rm -f /tmp/_cc.$$.*
CODE=$([ "$N200" = 1 ] && [ "$N404" = 5 ] && echo 200 || echo "200x$N200/404x$N404"); OUT=""; expect A6.19 "6 concurrent accepts of max_uses=1: exactly one 200, five 404" 200
req PATCH /api/workspaces/$W "$memT" '{"join_mode":"invite_only"}'; expect A7.1 "member cannot PATCH ws (403)" 403
req PATCH /api/workspaces/$W "$admT" '{"name":"Renamed"}'; expect A7.1b "admin renames ws" 200 "d['workspace']['name']=='Renamed'"
req PATCH /api/workspaces/$W "$OT" '{"join_mode":"bogus"}'; expect A7.1c "invalid join_mode (400)" 400
req PATCH /api/workspaces/$W "$OT" '{"join_mode":"invite_only"}'; expect A7.2 "owner sets invite_only" 200 "d['workspace']['join_mode']=='invite_only'"
reg "y$S"; YT=$TOK
req POST /api/channels/$PUB/join "$YT"; expect A7.3 "invite_only blocks non-member join (403)" 403
req POST /api/workspaces/$W/invitations "$OT" '{}'; IC=$(pyget "$OUT" 'd["invitation"]["code"]')
req POST /api/invitations/$IC/accept "$YT"; expect A7.4 "redeem invitation" 200
req POST /api/channels/$PUB/join "$YT"; expect A7.5 "after invite, join public ok" 200
req PATCH /api/workspaces/$W "$OT" '{"join_mode":"open"}'; reg "q$S"
req POST /api/channels/$PUB/join "$TOK"; expect A7.6 "back to open: outsider joins" 200

summary
