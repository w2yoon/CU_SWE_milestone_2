#!/bin/bash
source "$(dirname "$0")/lib.sh"
# --- A.1 auth, workspaces, members, open-join basics ---
S=$(uniq); reg "own$S"; OT=$TOK
req POST /api/auth/register "" "{\"username\":\"own$S\",\"password\":\"password123\"}"; expect A1.1 "duplicate username" 409
req POST /api/auth/register "" '{"username":"bad name","password":"password123"}'; expect A1.2 "username with space" 400
req POST /api/auth/register "" "{\"username\":\"sh$S\",\"password\":\"short\"}"; expect A1.3 "short password" 400
req POST /api/auth/login "" "{\"username\":\"own$S\",\"password\":\"wrongpass1\"}"; expect A1.4 "wrong password" 401
req GET /api/auth/me ""; expect A1.5 "me without token" 401
req GET /api/auth/me "bogus"; expect A1.6 "me invalid token" 401
req GET /api/auth/me "$OT"; expect A1.7 "me ok" 200 "d['user']['username']=='own$S'"
req POST /api/auth/register "" "{\"username\":\"tk$S\",\"password\":\"password123\"}"; expect A1.8 "register 201 user+token>=16" 201 "len(d['token'])>=16 and d['user']['username']=='tk$S'"
req POST /api/auth/login "" "{\"username\":\"tk$S\",\"password\":\"password123\"}"; expect A1.9 "login 200 token" 200 "len(d['token'])>=16"
W="w$S"
req POST /api/workspaces "$OT" "{\"slug\":\"$W\",\"name\":\"WS\"}"; expect A2.1 "create ws: open, #general, owner" 201 "d['workspace']['join_mode']=='open' and d['general_channel']['name']=='general' and d['workspace']['slug']=='$W'"
req POST /api/workspaces "$OT" "{\"slug\":\"$W\",\"name\":\"WS\"}"; expect A2.2 "duplicate slug" 409
req GET /api/workspaces "$OT"; expect A2.3 "list has ws" 200 "any(w['slug']=='$W' and w['owner_id']==d['workspaces'][0]['owner_id'] for w in d['workspaces'])"
req POST /api/workspaces "$OT" '{"slug":"Bad Slug","name":"x"}'; expect A2.4 "invalid slug" 400
req POST /api/workspaces "" '{"slug":"nologin","name":"x"}'; expect A2.5 "create ws no token" 401
req POST /api/workspaces "$OT" '[1]'; expect A2.7 "non-object JSON body -> 400" 400
mkws
req GET /api/workspaces "$outT"; expect A2.6 "list hides non-member ws" 200 "not any(w['slug']=='$W' for w in d['workspaces'])"
req GET /api/workspaces/$W/members "$OT"; expect A3.1 "members list with roles" 200 "len(d['members'])==4 and {m['user_id']:m['role'] for m in d['members']}[$OID]=='owner'"
req GET /api/workspaces/$W/members ""; expect A3.2 "members no token" 401
reg "z$S"; req POST /api/channels/$GEN/join "$TOK"; expect A3.3 "open ws: outsider joins public channel" 200
req GET /api/workspaces/$W/members "$OT"; expect A3.4 "outsider auto-onboarded as member" 200 "{m['user_id']:m['role'] for m in d['members']}[$UID_]=='member'"
req GET /api/workspaces "$TOK"; expect A3.5 "onboarded user sees ws" 200 "any(w['slug']=='$W' for w in d['workspaces'])"
req POST /api/channels/999999999/join "$TOK"; expect A3.6 "join unknown channel" 404
req POST /api/channels/$GEN/join ""; expect A3.7 "join no token" 401

# --- milestone 0 baseline (run last: kills node 1) ---
for i in 0 1 2; do req GET /api/health "" "" "http://127.0.0.1:800$i"; expect "M0.$((i+1))" "node $i health + node_id" 200 "d['status']=='ok' and d['node_id']==$i"; done
R=$(curl -s -m 3 -o /dev/null -w '%{http_code}' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "$BASE/api/ws?token=badtoken")
[ "$R" != 101 ] && ok M0.4 "bad-token WS rejected" || bad M0.4 "bad-token WS rejected" "expected not 101, got $R"
P1=$(ss -ltnpH 'sport = :8001' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
[ -n "$P1" ] && kill -9 $P1
for i in 0 2; do req GET /api/health "" "" "http://127.0.0.1:800$i"; expect "M0.5" "node $i alive after node 1 SIGKILL" 200 "d['status']=='ok'"; done
for _ in $(seq 10); do sleep 1; req GET /api/health "" "" "http://127.0.0.1:8001"; [ "$CODE" = 200 ] && break; done
expect "M0.6" "node 1 back within 10s (supervisor restart)" 200 "d['node_id']==1"

summary
