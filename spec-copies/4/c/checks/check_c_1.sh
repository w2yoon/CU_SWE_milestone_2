#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
from urllib.parse import quote
(u0, u1, u2), slug, ws, ch = setup(3)
(t0, i0), (t1, i1), (t2, i2) = u0, u1, u2
w = WS(8001, t2); sub = w.subscribe(ch) or {}
st, d = post(t0, ch, "react to me"); m = (d.get("message") or {}).get("id")
R = f"/api/messages/{m}/reactions"
r = http("POST", R, t0, {"emoji": "👍"})
expect("C1.1", "first reaction -> 200 {reactions}", 200, r, r[1].get("reactions") == [{"emoji": "👍", "count": 1, "user_ids": [i0]}])
r = http("POST", R, t0, {"emoji": "👍"})
expect("C1.2", "same reaction again is idempotent", 200, r, (r[1].get("reactions") or [{}])[0].get("count") == 1)
http("POST", R, t1, {"emoji": "👍"}); r = http("POST", R, t1, {"emoji": "🎉"}, port=8002)
rs = r[1].get("reactions") or []; by = {x.get("emoji"): x for x in rs}
expect("C1.3", "second user: 👍 count 2, 🎉 separate", 200, r, len(rs) == 2 and by.get("👍", {}).get("count") == 2 and sorted(by["👍"].get("user_ids", [])) == sorted([i0, i1]) and by.get("🎉", {}).get("count") == 1)
st, h = http("GET", f"/api/channels/{ch}/messages", t0)
mm = [x for x in h.get("messages", []) if x.get("id") == m]
check("C1.4", "history carries reactions", bool(mm) and mm[0].get("reactions") == rs, mm[:1])
r = http("DELETE", f"{R}/{quote('🎉')}", t1)
expect("C1.5", "remove reaction -> 200, emoji with 0 disappears", 200, r, [x["emoji"] for x in r[1].get("reactions", [])] == ["👍"])
a = http("POST", R, None, {"emoji": "x"}); b = http("POST", "/api/messages/999999999/reactions", t0, {"emoji": "x"}); c = http("POST", R, t0, {"emoji": ""})
check("C1.6", "no token 401 / unknown message 404 / empty emoji 400", (a[0], b[0], c[0]) == (401, 404, 400), (a[0], b[0], c[0]))
st, d = http("POST", f"/api/workspaces/{slug}/channels", t0, {"name": "p" + S, "is_private": True}); pc = (d.get("channel") or {}).get("id")
st, d = post(t0, pc, "secret"); pm = (d.get("message") or {}).get("id")
expect("C1.7", "non-member reacts in private channel -> 403", 403, http("POST", f"/api/messages/{pm}/reactions", t1, {"emoji": "👍"}))
ev = [e for e in w.drain(0.8, 3) if "event_id" in e]
k = [e["type"] for e in ev if e["type"].startswith("reaction.")]
ids = [e["event_id"] for e in ev]
want = ["reaction.added", "reaction.added", "reaction.added", "reaction.removed"]
check("C1.8", "WS gets reaction.added x3 then reaction.removed, dense ids, fields", k == want and ids == list(range(ids[0], ids[0] + len(ids))) and all(x in ev[-1] for x in ("message_id", "emoji", "user_id", "channel_id")), (k, ids))
st, d = post(t0, ch, "to archive"); m2 = (d.get("message") or {}).get("id")
http("POST", f"/api/channels/{ch}/archive", t0)
expect("C1.9", "reaction in archived channel -> 423", 423, http("POST", f"/api/messages/{m2}/reactions", t0, {"emoji": "👍"}))
http("POST", f"/api/channels/{ch}/unarchive", t0)
a = http("POST", "/api/dms", t0, {"user_id": i1})
dm = a[1].get("channel_id")
expect("C1.10", "create DM -> 200 {channel_id}", 200, a, dm is not None)
b = http("POST", "/api/dms", t1, {"user_id": i0}, port=8001); c = http("POST", "/api/dms", t0, {"user_id": i1})
check("C1.11", "same DM id both directions and repeated", b[1].get("channel_id") == dm and c[1].get("channel_id") == dm, (dm, b, c))
x = http("POST", "/api/dms", t0, {"user_id": 999999999}); y = http("POST", "/api/dms", t0, {}); z = http("POST", "/api/dms", None, {"user_id": i1})
check("C1.12", "DM unknown user 404 / missing 400 / no token 401", (x[0], y[0], z[0]) == (404, 400, 401), (x[0], y[0], z[0]))
p = post(t0, dm, "hi dm"); g = http("GET", f"/api/channels/{dm}/messages", t1)
check("C1.13", "DM members post 201 and read 200", p[0] == 201 and g[0] == 200 and len(g[1].get("messages", [])) == 1, (p[0], g[0]))
g2 = http("GET", f"/api/channels/{dm}/messages", t2); p2 = post(t2, dm, "intruder")
check("C1.14", "third user cannot read/post DM -> 403", (g2[0], p2[0]) == (403, 403), (g2[0], p2[0]))
finish()
PY
