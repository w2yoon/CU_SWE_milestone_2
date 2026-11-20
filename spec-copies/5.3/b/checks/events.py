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
