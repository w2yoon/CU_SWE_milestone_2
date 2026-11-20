#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
t0, i0 = reg("p" + S); t1, i1 = reg("q" + S)
F7 = ("id", "username", "display_name", "timezone", "avatar_url", "status_text", "status_emoji")
r = http("GET", f"/api/users/{i0}", t1); expect("C5.1", "get user -> UserObj", 200, r, all(k in (r[1].get("user") or {}) for k in F7))
a = http("GET", "/api/users/999999999", t1); b = http("GET", f"/api/users/{i0}")
check("C5.2", "unknown 404 / no token 401", (a[0], b[0]) == (404, 401), (a[0], b[0]))
w = WS(8001, t1); time.sleep(0.3)
new = {"display_name": "Pat Q", "timezone": "Europe/Paris", "status_text": "busy", "status_emoji": ":tada:", "avatar_url": "https://example.com/a.png"}
r = http("PATCH", "/api/users/me", t0, new); g = http("GET", f"/api/users/{i0}", t1, port=8002)
expect("C5.3", "patch profile, persisted across nodes", 200, r, all((r[1].get("user") or {}).get(k) == v and (g[1].get("user") or {}).get(k) == v for k, v in new.items()))
ev = [m for m in w.drain(0.8, 2) if m.get("type") == "user.updated"]
check("C5.7", "user.updated broadcast to an unsubscribed socket on another node, no channel_id/event_id", len(ev) >= 1 and (ev[0].get("user") or {}).get("id") == i0 and "channel_id" not in ev[0] and "event_id" not in ev[0], ev[:1])
r = http("PATCH", "/api/users/me", t0, {"status_text": "away"})
expect("C5.4", "partial patch keeps other fields", 200, r, (r[1].get("user") or {}).get("display_name") == "Pat Q" and r[1]["user"].get("status_text") == "away")
expect("C5.5", "invalid timezone -> 400", 400, http("PATCH", "/api/users/me", t0, {"timezone": "Mars/Olympus"}))
bads = [{"display_name": ""}, {"display_name": "x" * 81}, {"status_text": "x" * 141}, {"status_emoji": "x" * 65}, {"avatar_url": "x" * 513}, {"display_name": 5}]
codes = [http("PATCH", "/api/users/me", t0, b)[0] for b in bads]
g = http("GET", f"/api/users/{i0}", t0)
check("C5.6", "length/type violations -> 400, nothing saved", codes == [400] * 6 and (g[1].get("user") or {}).get("display_name") == "Pat Q", codes)
finish()
PY
