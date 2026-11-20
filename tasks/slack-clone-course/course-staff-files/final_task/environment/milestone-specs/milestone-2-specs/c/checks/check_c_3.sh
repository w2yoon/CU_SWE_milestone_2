#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0, u1), slug, ws, ch = setup(2)
(t0, i0), (t1, i1) = u0, u1
tb, ib = reg("bob" + S)
http("POST", f"/api/channels/{(http('POST', f'/api/workspaces/{slug}/channels', t0, {'name': 'o' + S})[1].get('channel') or {}).get('id')}/join", tb)
SH = "¯\\_(ツ)_/¯"
r = post(t0, ch, "/me waves"); expect("C3.1", "/me keeps literal body", 201, r, (r[1].get("message") or {}).get("body") == "/me waves")
a, b = post(t0, ch, "/me"), post(t0, ch, "/me   "); check("C3.2", "/me without text -> 400", (a[0], b[0]) == (400, 400), (a[0], b[0]))
r = post(t0, ch, "/shrug hi"); expect("C3.3", "/shrug text appends emoticon", 201, r, (r[1].get("message") or {}).get("body") == "hi " + SH)
r = post(t0, ch, "/shrug"); expect("C3.4", "/shrug alone", 201, r, (r[1].get("message") or {}).get("body") == SH)
r = post(t0, ch, "/topic New topic"); t = post(t0, ch, "/topic " + "x" * 251)
expect("C3.5", "/topic -> 201 message:null channel.topic; >250 -> 400", 201, r, "message" in r[1] and r[1]["message"] is None and (r[1].get("channel") or {}).get("topic") == "New topic" and t[0] == 400)
r = post(t0, ch, f"/invite @bob{S}"); st, mem = http("GET", f"/api/channels/{ch}/members", t0)
u = post(t0, ch, "/invite @nosuch" + S)
expect("C3.6", "/invite -> 201 invited_user, now member; unknown -> 404", 201, r, r[1].get("message", 1) is None and (r[1].get("invited_user") or {}).get("id") == ib and any(x.get("id") == ib for x in mem.get("members", [])) and u[0] == 404)
x = post(t1, ch, "/archive"); check("C3.9", "/archive by non-owner -> 403", x[0] == 403, x)
r = post(t0, ch, "/archive"); n = post(t0, ch, "normal")
expect("C3.7", "/archive -> 201 channel archived, then post 423", 201, r, r[1].get("message", 1) is None and (r[1].get("channel") or {}).get("is_archived") is True and n[0] == 423)
y = post(t1, ch, "/unarchive")
r = post(t0, ch, "/unarchive")
expect("C3.8", "/unarchive while archived -> 201 is_archived false", 201, r, (r[1].get("channel") or {}).get("is_archived") is False and y[0] == 403)
expect("C3.10", "unknown command -> 400", 400, post(t0, ch, "/bogus thing"))
r = post(t0, ch, "//literal text"); expect("C3.11", "//literal posted as-is", 201, r, (r[1].get("message") or {}).get("body") == "//literal text")
finish()
PY
