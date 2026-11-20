#!/bin/bash
# A and B regression suite: re-tests every A and B rule, prints only what broke, then REGRESSION: N broken.
FEEDBACK=verbose
BASE=${BASE:-http://127.0.0.1:8000}
PASS=0; FAIL=0; SECONDS=0; BUDGET=${BUDGET:-24}
uniq() { echo "$BASHPID$RANDOM$RANDOM"; }
pyget() { python3 -c 'import sys,json;d=json.loads(sys.argv[1]);print(eval(sys.argv[2]))' "$1" "$2" 2>/dev/null; }
healthy() { for p in 8000 8001 8002; do curl -fs -m 2 127.0.0.1:$p/api/health >/dev/null || return 1; done; }
boot() {
  healthy && return
  (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 </dev/null &) >/dev/null 2>&1
  for _ in $(seq 20); do healthy && return; sleep 1; done
  [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL cluster not answering /api/health on 8000-8002 (see /tmp/start.log)"
  echo "REGRESSION: 1 broken"; exit 1
}
# req METHOD PATH [TOKEN] [JSON_BODY] [BASE_URL] -> sets CODE and OUT
req() {
  if [ $SECONDS -ge $BUDGET ]; then [ "$FEEDBACK" = verbose ] && echo "ABORT time budget ${BUDGET}s exceeded (a request hangs or crashes)"; FAIL=$((FAIL+1)); summary; exit 1; fi
  local m=$1 p=$2 t=$3 d=$4 b=${5:-$BASE} f=/tmp/_b.$BASHPID
  local a=(-s -m 2 -o $f -w '%{http_code}' -X "$m" "$b$p" -H 'Content-Type: application/json')
  [ -n "$t" ] && a+=(-H "Authorization: Bearer $t")
  [ -n "$d" ] && a+=(-d "$d")
  CODE=$(curl "${a[@]}"); OUT=$(cat $f 2>/dev/null)
}
ok()  { PASS=$((PASS+1)); return 0; }
bad() { FAIL=$((FAIL+1)); [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL $SEC $1 $2: $3"; return 0; }
# expect ID DESC WANT_CODE [python expr on parsed body d]
expect() {
  local good=1
  [ "$CODE" = "$3" ] || good=0
  if [ $good = 1 ] && [ -n "$4" ]; then [ "$(pyget "$OUT" "$4")" = "True" ] || good=0; fi
  if [ $good = 1 ]; then ok "$1" "$2"; else bad "$1" "$2" "expected $3${4:+ and $4}, got $CODE ${OUT:0:100}"; fi
}
reg() {
  req POST /api/auth/register "" "{\"username\":\"$1\",\"password\":\"password123\"}"
  TOK=$(pyget "$OUT" 'd["token"]'); UID_=$(pyget "$OUT" 'd["user"]["id"]')
}
summary() { echo "__BROKEN $FAIL"; }
# mkws: workspace with owner(O) admin(adm) member(mem) guest(gst) outsider(out)
mkws() {
  S=$(uniq); reg "o$S"; OT=$TOK; OID=$UID_; W="w$S"
  req POST /api/workspaces "$OT" "{\"slug\":\"$W\",\"name\":\"WS $S\"}"
  GEN=$(pyget "$OUT" 'd["general_channel"]["id"]'); WID=$(pyget "$OUT" 'd["workspace"]["id"]')
  reg "adm$S"; admT=$TOK; admI=$UID_
  reg "mem$S"; memT=$TOK; memI=$UID_
  reg "gst$S"; gstT=$TOK; gstI=$UID_
  reg "out$S"; outT=$TOK; outI=$UID_
  for t in $admT $memT $gstT; do req POST /api/channels/$GEN/join "$t"; done
  req PATCH /api/workspaces/$W/members/$admI "$OT" '{"role":"admin"}'
  req PATCH /api/workspaces/$W/members/$gstI "$OT" '{"role":"guest"}'
}
mkch() {
  local p=false; [ "$3" = private ] && p=true
  req POST /api/workspaces/$W/channels "$1" "{\"name\":\"$2\",\"is_private\":$p}"
  CH=$(pyget "$OUT" 'd["channel"]["id"]')
}
post() {
  local d="{\"body\":\"$3\"}"; [ -n "$4" ] && d="{\"body\":\"$3\",\"parent_id\":$4}"
  req POST /api/channels/$2/messages "$1" "$d"; MID=$(pyget "$OUT" 'd["message"]["id"]')
}
cat > /tmp/regression_events.py <<'PYEOF'
#!/usr/bin/env python3
"""B.4 checks: durable events, dense event_id, cross-node fan-out, resume. Stdlib only."""
import json, socket, struct, os, base64, time, random, urllib.request, urllib.error
import atexit
P = F = 0
SEEN = set()
ALL = ["B4.%d" % i for i in range(1, 13)]
def _final():
    global F
    for i in ALL:
        if i not in SEEN:
            F += 1; print(f"FAIL {i} not run (check script stopped early or crashed)")
    print(f"EVENTS: {P} passed, {F} failed")
atexit.register(_final)
def res(ok, id_, desc, extra=""):
    global P, F
    SEEN.add(id_)
    if ok: P += 1; print(f"PASS {id_} {desc}")
    else: F += 1; print(f"FAIL {id_} {desc} {str(extra)[:120]}")
def http(method, path, token=None, body=None, port=8000):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=3) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b"{}")
        except Exception: return e.code, {}
class WS:
    def __init__(self, port, token):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=3)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /api/ws?token={token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf: buf += self.s.recv(1)
        self.ok = b" 101 " in buf.split(b"\r\n")[0]
        self.buf = b""
    def send(self, obj):
        raw = json.dumps(obj).encode(); mask = os.urandom(4); n = len(raw)
        hdr = b"\x81" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
    def _need(self, n, deadline):
        while len(self.buf) < n:
            self.s.settimeout(max(0.05, deadline - time.time()))
            c = self.s.recv(4096)
            if not c: raise EOFError
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]; return out
    def recv(self, timeout=1.0):
        deadline = time.time() + timeout
        try:
            while True:
                h = self._need(2, deadline); op = h[0] & 15; n = h[1] & 127
                if n == 126: n = struct.unpack(">H", self._need(2, deadline))[0]
                elif n == 127: n = struct.unpack(">Q", self._need(8, deadline))[0]
                data = self._need(n, deadline)
                if op == 8: return None
                if op in (9, 10): continue
                return json.loads(data)
        except (socket.timeout, EOFError, OSError, ValueError): return None
    def drain(self, idle=1.5, limit=8.0):
        out = []; end = time.time() + limit; last = time.time()
        while time.time() < end and time.time() - last < idle:
            m = self.recv(0.3)
            if m is not None: out.append(m); last = time.time()
        return out
def reg(name):
    c, d = http("POST", "/api/auth/register", body={"username": name, "password": "password123"})
    return d["token"], d["user"]["id"]
S = str(random.randint(10**8, 10**9)); ports = (8000, 8001, 8002)
t1, u1 = reg("ev" + S); t2, u2 = reg("evm" + S)
c, d = http("POST", "/api/workspaces", t1, {"slug": "ev" + S, "name": "E"}); gen = d["general_channel"]["id"]
c, d = http("POST", f"/api/workspaces/ev{S}/channels", t1, {"name": "chan" + S}); CH = d["channel"]["id"]
w0, w2 = WS(8000, t1), WS(8002, t1)
res(w0.ok and w2.ok, "B4.1", "authenticated WS accepted on nodes 0 and 2")
w0.send({"type": "subscribe", "channel_id": CH}); w2.send({"type": "subscribe", "channel_id": CH})
a, b = w0.recv(2), w2.recv(2)
res(bool(a) and a.get("type") == "subscribed" and isinstance(a.get("head_event_id"), int) and a.get("channel_id") == CH, "B4.2", "subscribed frame with int head_event_id", a)
head = (a or {}).get("head_event_id", 0)
http("POST", f"/api/channels/{CH}/join", t2, port=8001)                       # member.joined
c, m1 = http("POST", f"/api/channels/{CH}/messages", t1, {"body": "one"}, 8000); m1 = m1.get("message", {}).get("id")
c, m2 = http("POST", f"/api/channels/{CH}/messages", t1, {"body": "two"}, 8001); m2 = m2.get("message", {}).get("id")
c, m3 = http("POST", f"/api/channels/{CH}/messages", t1, {"body": "three"}, 8002)
c, r1 = http("POST", f"/api/channels/{CH}/messages", t1, {"body": "re", "parent_id": m1}, 8001)
http("PATCH", f"/api/messages/{m1}", t1, {"body": "one edited"}, 8002)
http("DELETE", f"/api/messages/{m2}", t1, None, 8000)
http("POST", f"/api/channels/{CH}/leave", t2, {}, 8001)                      # member.left
http("POST", f"/api/channels/{CH}/archive", t1, {}, 8002)
http("POST", f"/api/channels/{CH}/unarchive", t1, {}, 8000)
e0 = [m for m in w0.drain() if "event_id" in m]; e2 = [m for m in w2.drain() if "event_id" in m]
ids0 = [m["event_id"] for m in e0]; ids2 = [m["event_id"] for m in e2]
types = {m["type"] for m in e0}
want = {"member.joined", "message.created", "message.reply", "message.updated", "message.deleted", "member.left", "channel.archived", "channel.unarchived"}
res(want <= types, "B4.3", "all durable kinds delivered", sorted(want - types))
res(ids0 == list(range(head + 1, head + 1 + len(ids0))) and len(ids0) >= 10, "B4.4", "event_id dense: head+1.. no gaps/dups", ids0)
res(ids0 == ids2 and len(ids0) > 0, "B4.5", "same sequence observed via node 0 and node 2", (ids0, ids2))
res(all(m.get("channel_id") == CH for m in e0), "B4.6", "frames carry channel_id")
tm = {m["event_id"]: m["type"] for m in e0}
res(tm.get(ids0[-1] if ids0 else -1) == "channel.unarchived" and "message.created" in types, "B4.7", "last event is channel.unarchived (HTTP order == event order)", tm)
w1 = WS(8001, t1); w1.send({"type": "resume", "channel_id": CH, "since_event_id": head})
fr = w1.drain(1.0); rid = [m["event_id"] for m in fr if "event_id" in m]; rs = [m for m in fr if m.get("type") == "resumed"]
res(rid == ids0, "B4.8", "resume from other node replays same events", (rid, ids0))
res(len(rs) == 1 and rs[0].get("head_event_id") == (ids0[-1] if ids0 else None) and fr and fr[-1].get("type") == "resumed", "B4.9", "resumed frame last, head_event_id==last id", rs)
w1.send({"type": "resume", "channel_id": CH, "since_event_id": (ids0[-1] - 2) if ids0 else 0}); fr = w1.drain(1.0)
res([m["event_id"] for m in fr if "event_id" in m] == ids0[-2:], "B4.10", "resume since=last-2 returns exactly 2 events", [m.get("type") for m in fr])
w3 = WS(8000, t1); w3.send({"type": "subscribe", "channel_id": CH}); s = w3.recv(2)
res(bool(s) and s.get("head_event_id") == (ids0[-1] if ids0 else None), "B4.11", "new subscribe head_event_id == latest event", s)
http("POST", f"/api/channels/{CH}/messages", t1, {"body": "later"}, 8002)
live = [m for m in w3.drain(1.0) if "event_id" in m]
res(len(live) == 1 and live[0]["event_id"] == (ids0[-1] + 1 if ids0 else -1) and live[0]["type"] == "message.created", "B4.12", "cross-node live fan-out (post on 8002, receive on 8000)", live)
PYEOF
sec_a_1() {
SEC=roles
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
}
sec_a_2() {
SEC=roles
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
}
sec_a_3() {
SEC=roles
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
}
sec_b_1() {
SEC=channels
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
}
sec_b_2() {
SEC=channels
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
}
sec_b_3() {
SEC=channels
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
}
sec_b_4() {
SEC=channels
# --- B.4 event ordering over WebSocket / cluster ---
EV=$(timeout 25 python3 /tmp/regression_events.py 2>&1)
while read -r l; do
  case "$l" in PASS*) PASS=$((PASS+1));; FAIL*) FAIL=$((FAIL+1));; *) continue;; esac
  [ "$FEEDBACK" = verbose ] && echo "$l"
done <<<"$EV"
echo "$EV" | grep -q '^EVENTS: ' || bad B4.0 "events.py did not finish" "${EV: -300}"
}
boot
R=/tmp/regression.$$; rm -f $R.*
for s in a_1 a_2 a_3 b_1 b_2 b_3 b_4; do ( SECONDS=0; sec_$s; summary ) > $R.$s 2>&1 & done; wait
( SEC=baseline; SECONDS=0; # --- milestone 0 baseline (run last: kills node 1) ---
for i in 0 1 2; do req GET /api/health "" "" "http://127.0.0.1:800$i"; expect "M0.$((i+1))" "node $i health + node_id" 200 "d['status']=='ok' and d['node_id']==$i"; done
R=$(curl -s -m 3 -o /dev/null -w '%{http_code}' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "$BASE/api/ws?token=badtoken")
[ "$R" != 101 ] && ok M0.4 "bad-token WS rejected" || bad M0.4 "bad-token WS rejected" "expected not 101, got $R"
P1=$(ss -ltnpH 'sport = :8001' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
[ -n "$P1" ] && kill -9 $P1
for i in 0 2; do req GET /api/health "" "" "http://127.0.0.1:800$i"; expect "M0.5" "node $i alive after node 1 SIGKILL" 200 "d['status']=='ok'"; done
for _ in $(seq 10); do sleep 1; req GET /api/health "" "" "http://127.0.0.1:8001"; [ "$CODE" = 200 ] && break; done
expect "M0.6" "node 1 back within 10s (supervisor restart)" 200 "d['node_id']==1"
summary ) > $R.zz 2>&1
grep -h '^REGRESSION FAIL' $R.* ; N=$(cat $R.* | grep '^__BROKEN' | awk '{s+=$2} END {print s+0}')
for s in a_1 a_2 a_3 b_1 b_2 b_3 b_4 zz; do grep -q '^__BROKEN' $R.$s || { N=$((N+1)); [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL $s section did not finish"; }; done
rm -f $R.*
echo "REGRESSION: $N broken"; [ "$N" -eq 0 ]
