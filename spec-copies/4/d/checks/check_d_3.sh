#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0,), slug, ws, ch = setup(1); t0 = u0[0]
wait(redis_ping, 5)
a, b = WS(8000, t0), WS(8001, t0)
head = (a.subscribe(ch) or {}).get("head_event_id", -1); b.subscribe(ch)
t = time.time(); pid = kill_port(6379)
p = [post(t0, ch, f"r{i}", port=8000 + i)[0] for i in range(3)]
check("D3.1", "writes on all nodes succeed within 5 s of redis kill", pid is not None and p == [201] * 3 and time.time() - t < 5, (pid, p, round(time.time() - t, 2)))
ea, eb = [[f["event_id"] for f in msgs(s.drain(0.8, 4))] for s in (a, b)]
check("D3.2", "both subscribers receive all 3 frames", len(ea) == 3 and len(eb) == 3, (ea, eb))
check("D3.3", "ids dense and identical across sockets", ea == eb == [head + 1, head + 2, head + 3], (head, ea, eb))
check("D3.4", "redis respawned within 5 s", wait(redis_ping, max(0.1, 5 - (time.time() - t))), "no +PONG")
post(t0, ch, "after", port=8002)
ea, eb = [[f["event_id"] for f in msgs(s.drain(0.8, 3))] for s in (a, b)]
check("D3.5", "fan-out after respawn", ea == eb == [head + 4], (ea, eb))
finish()
PY
