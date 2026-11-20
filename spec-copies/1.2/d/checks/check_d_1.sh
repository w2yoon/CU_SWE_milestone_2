#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
hs = [http("GET", "/api/health", port=8000 + i) for i in range(3)]
check("D1.1", "health + node_id on 3 nodes", all(h[0] == 200 and h[1].get("status") == "ok" and h[1].get("node_id") == i for i, h in enumerate(hs)), [h[1] for h in hs])
check("D1.2", "redis answers PING on 6379", redis_ping(), "no +PONG on 127.0.0.1:6379")
w = WS(8000, "bad-token-xyz"); check("D1.3", "invalid-token WebSocket rejected", not w.ok, "got 101")
(u0, u1), slug, ws, ch = setup(2); (t0, i0), (t1, i1) = u0, u1
kill_port(8001); time.sleep(0.3)
r = [http("GET", "/api/auth/me", t0, port=p)[0] for p in (8000, 8002)] + [http("GET", f"/api/channels/{ch}/messages", t1, port=p)[0] for p in (8000, 8002)]
p = [post(t0, ch, f"during outage {i}", port=pt)[0] for i, pt in enumerate((8000, 8002))]
check("D1.4", "nodes 8000/8002 serve reads+writes while 8001 is dead", r == [200] * 4 and p == [201, 201], (r, p))
back = wait_health(8001, 10)
h = http("GET", f"/api/channels/{ch}/messages", t1, port=8001)
check("D1.5", "8001 back within 10 s and sees outage writes", back and sum("during outage" in m.get("body", "") for m in h[1].get("messages", [])) == 2, (back, h[0]))
pid = kill_port(6379); time.sleep(0.2)
check("D1.6", "redis respawned within 5 s after SIGKILL", pid is not None and wait(redis_ping, 5), f"pid={pid}")
finish()
PY
