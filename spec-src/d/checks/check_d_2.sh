#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0,), slug, ws, ch = setup(1); t0 = u0[0]
for p in (8000, 8001, 8002): wait_health(p, 8)
a, c = WS(8000, t0), WS(8002, t0)
head = (a.subscribe(ch) or {}).get("head_event_id", -1); c.subscribe(ch)
kill_port(8001); time.sleep(0.3)
check("D2.1", "sockets on 8000/8002 survive killing 8001", a.ok and c.ok and http("GET", "/api/health", port=8000)[0] == 200, "")
p = [post(t0, ch, "x1", port=8000)[0], post(t0, ch, "x2", port=8002)[0]]
ea, ec = [[f["event_id"] for f in msgs(s.drain(0.8, 3))] for s in (a, c)]
check("D2.2", "writes 201 while 8001 down; both subscribers get dense ids", p == [201, 201] and ea == ec == [head + 1, head + 2], (p, ea, ec))
r = WS(8002, t0); r.send({"type": "resume", "channel_id": ch, "since_event_id": head}); fr = r.drain(0.8, 3)
check("D2.3", "resume on 8002 replays both, then resumed", [f.get("event_id") for f in fr if "event_id" in f] == [head + 1, head + 2] and fr and fr[-1].get("type") == "resumed", [f.get("type") for f in fr])
wait_health(8001, 10)
kill_port(8000); time.sleep(0.3)
q = [post(t0, ch, f"y{i}", port=8002)[0] for i in range(2)]
n = WS(8001, t0); n.send({"type": "resume", "channel_id": ch, "since_event_id": head + 2}); fr = n.drain(0.8, 3)
check("D2.4", "after 8000 dies, resume on 8001 gets the 2 missed events", q == [201, 201] and [f.get("event_id") for f in fr if "event_id" in f] == [head + 3, head + 4] and fr[-1:] and fr[-1].get("type") == "resumed", (q, [f.get("type") for f in fr]))
c.drain(0.3, 1)
post(t0, ch, "z", port=8001); ev = [f["event_id"] for f in msgs(c.drain(0.8, 3))]
check("D2.5", "post on restarted 8001 reaches subscriber on 8002 live", ev == [head + 5], ev)
wait_health(8000, 10)
hs = [(WS(p, t0).subscribe(ch) or {}).get("head_event_id") for p in (8000, 8001, 8002)]
check("D2.6", "same head_event_id on all three nodes", hs == [head + 5] * 3, hs)
finish()
PY
