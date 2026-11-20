#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0, u1, u2, u3), slug, ws, ch = setup(4)
(t0, i0), (tA, iA), (tB, iB), (tC, iC) = u0, u1, u2, u3
http("POST", f"/api/channels/{ch}/leave", tC)
G = f"/api/workspaces/{slug}/groups"
r = http("POST", G, tA, {"handle": "devs" + S, "name": "Devs", "user_ids": [i0, iA, iB, iC]}); g = r[1].get("group") or {}
expect("C4.1", "member creates group -> 201 GroupObj", 201, r, g.get("handle") == "devs" + S and sorted(g.get("member_user_ids") or []) == sorted([i0, iA, iB, iC]) and "members" not in g and "id" in g)
a = http("POST", G, tA, {"handle": "devs" + S, "name": "x"}); b = http("POST", G, tA, {"handle": "Bad Handle", "name": "x"}); c = http("POST", G, tA, {"handle": "ok" + S})
check("C4.2", "dup handle 409 / bad handle 400 / missing name 400", (a[0], b[0], c[0]) == (409, 400, 400), (a[0], b[0], c[0]))
to, _ = reg("out" + S)
a = http("POST", G, to, {"handle": "x" + S, "name": "x"}); b = http("POST", G, None, {"handle": "y" + S, "name": "y"}); c = http("POST", "/api/workspaces/nosuch" + S + "/groups", tA, {"handle": "z", "name": "z"})
check("C4.3", "outsider 403 / no token 401 / unknown slug 404", (a[0], b[0], c[0]) == (403, 401, 404), (a[0], b[0], c[0]))
r = http("GET", G, tB); expect("C4.4", "list groups", 200, r, any(x.get("handle") == "devs" + S for x in r[1].get("groups", [])))
r = http("PATCH", f"{G}/devs{S}", tA, {"name": "Developers", "user_ids": [i0, iA, iB, iC]})
expect("C4.5", "creator patches group", 200, r, (r[1].get("group") or {}).get("name") == "Developers")
a = http("PATCH", f"{G}/devs{S}", tB, {"name": "hack"}); b = http("PATCH", f"{G}/devs{S}", t0, {"name": "Devs"}); c = http("PATCH", f"{G}/nosuch", tA, {"name": "x"})
check("C4.6", "other member 403 / owner 200 / unknown 404", (a[0], b[0], c[0]) == (403, 200, 404), (a[0], b[0], c[0]))
http("POST", G, tA, {"handle": "tmp" + S, "name": "Tmp", "user_ids": [iA]})
a = http("DELETE", f"{G}/tmp{S}", tB); b = http("DELETE", f"{G}/tmp{S}", tA); c = http("DELETE", f"{G}/tmp{S}", tA)
check("C4.7", "delete: other 403 / creator 200 deleted / again 200", (a[0], b[0], c[0]) == (403, 200, 200) and b[1].get("deleted") is True and c[1].get("deleted") is True, (a, b, c))
def ment(r): return sorted((r[1].get("message") or {}).get("mentions") or [])
r = post(t0, ch, f"@devs{S} ship it"); expect("C4.8", "@group -> members in channel, minus author", 201, r, ment(r) == sorted([iA, iB]), body=ment(r))
r = post(t0, ch, f"@devs{S} and @u1x{S}"); expect("C4.9", "group + username deduped", 201, r, ment(r) == sorted([iA, iB]), body=ment(r))
wa, w0 = WS(8001, tA), WS(8002, t0); time.sleep(0.5)
r = post(t0, ch, "@here look"); expect("C4.10", "@here -> connected members only, minus author", 201, r, ment(r) == [iA], body=ment(r))
wa.close(); time.sleep(1.0)
r = post(t0, ch, "@here again"); expect("C4.11", "@here after socket closed -> []", 201, r, ment(r) == [], body=ment(r))
finish()
PY
