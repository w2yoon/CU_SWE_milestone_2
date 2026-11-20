#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
import threading, statistics
(u0,), slug, ws, ch = setup(1); t0 = u0[0]
N = 25; subs = [WS(8000 + i, t0) for i in range(3)]
for s in subs: s.subscribe(ch)
sent, got, codes = {}, [{} for _ in subs], []
def reader(i):
    end = time.time() + 14
    while time.time() < end and len(got[i]) < 3 * N:
        m = subs[i].recv(0.3)
        if m and m.get("type") == "message.created":
            got[i][(m.get("message") or {}).get("body") or m.get("body")] = (time.time(), m.get("event_id"))
def writer(n):
    for k in range(N):
        b = f"perf-{n}-{k}"; sent[b] = time.time()
        codes.append(post(t0, ch, b, port=8000 + n)[0])
rs = [threading.Thread(target=reader, args=(i,)) for i in range(3)]; ws_ = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
for t in rs + ws_: t.start()
for t in ws_: t.join()
for t in rs: t.join()
check("D4.1", "all 75 concurrent POSTs -> 201", codes.count(201) == 3 * N, {c: codes.count(c) for c in set(codes)})
lat = [[(got[i][b][0] - sent[b]) * 1000 for b in sent if b in got[i]] for i in range(3)]
check("D4.2", "each subscriber got >= 99% of frames (frames carry message.body)", all(len(l) >= 0.99 * 3 * N for l in lat), [len(l) for l in lat])
med = [round(statistics.median(l)) if l else 9999 for l in lat]
p95 = [round(sorted(l)[int(len(l) * 0.95) - 1]) if l else 9999 for l in lat]
check("D4.3", "median latency <= 300 ms per subscriber", all(m <= 300 for m in med), f"median ms {med}")
check("D4.4", "p95 latency <= 800 ms per subscriber", all(m <= 800 for m in p95), f"p95 ms {p95}")
inc = [[v[1] for v in sorted(g.values(), key=lambda v: v[0])] for g in got]
check("D4.5", "ids strictly increasing per subscriber", all(x == sorted(set(x)) for x in inc), [x[:5] for x in inc])
finish()
PY
