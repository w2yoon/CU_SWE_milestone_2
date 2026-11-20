#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0, u1, u2), slug, ws, ch = setup(3)
(t0, i0), (t1, i1), (t2, i2) = u0, u1, u2
w = WS(8002, t0); head = (w.subscribe(ch) or {}).get("head_event_id", -1)
m = (post(t1, ch, "orig")[1].get("message") or {})
r = http("PATCH", f"/api/messages/{m.get('id')}", t1, {"body": "new"}, port=8001); mm = r[1].get("message") or {}
expect("E1.1", "author edits -> 200, edited_at set", 200, r, mm.get("body") == "new" and str(mm.get("edited_at") or "").endswith("Z") and m.get("edited_at") is None)
h = [x for x in http("GET", f"/api/channels/{ch}/messages", t2)[1].get("messages", []) if x.get("id") == m.get("id")]
check("E1.2", "history shows edit", bool(h) and h[0].get("body") == "new" and h[0].get("edited_at"), h[:1])
a = http("PATCH", f"/api/messages/{m.get('id')}", t0, {"body": "owner"}); b = http("PATCH", f"/api/messages/{m.get('id')}", t2, {"body": "x"})
check("E1.3", "non-author (even owner) edit -> 403", (a[0], b[0]) == (403, 403), (a[0], b[0]))
a = http("PATCH", f"/api/messages/{m.get('id')}", t1, {"body": "  "}); b = http("PATCH", "/api/messages/999999999", t1, {"body": "x"}); c = http("PATCH", f"/api/messages/{m.get('id')}", None, {"body": "x"})
check("E1.4", "blank 400 / unknown 404 / no token 401", (a[0], b[0], c[0]) == (400, 404, 401), (a[0], b[0], c[0]))
r = http("DELETE", f"/api/messages/{m.get('id')}", t1); expect("E1.5", "author deletes -> 200 deleted", 200, r, r[1].get("deleted") is True)
r = http("DELETE", f"/api/messages/{m.get('id')}", t1, port=8002); expect("E1.6", "delete idempotent", 200, r, r[1].get("deleted") is True)
p = (post(t1, ch, "parent")[1].get("message") or {}).get("id"); rp = (post(t1, ch, "reply", parent_id=p)[1].get("message") or {}).get("id")
http("DELETE", f"/api/messages/{rp}", t1)
h = http("GET", f"/api/channels/{ch}/messages", t2)[1].get("messages", [])
check("E1.7", "deleted gone from history; reply_count drops", not any(x.get("id") == m.get("id") for x in h) and [x.get("reply_count") for x in h if x.get("id") == p] == [0], [(x.get("id"), x.get("reply_count")) for x in h][:4])
expect("E1.8", "edit deleted message -> 409", 409, http("PATCH", f"/api/messages/{m.get('id')}", t1, {"body": "zombie"}))
q = (post(t1, ch, "q")[1].get("message") or {}).get("id"); q2 = (post(t1, ch, "q2")[1].get("message") or {}).get("id")
a = http("DELETE", f"/api/messages/{q}", t0); b = http("DELETE", f"/api/messages/{q2}", t2)
check("E1.9", "owner deletes other's 200 / plain member 403", (a[0], b[0]) == (200, 403), (a[0], b[0]))
ev = [e for e in w.drain(0.8, 3) if "event_id" in e]; ids = [e["event_id"] for e in ev]
up = [e for e in ev if e["type"] == "message.updated"]; de = [e for e in ev if e["type"] == "message.deleted"]
check("E1.10", "WS message.updated (message) + message.deleted (message_id), dense", up and "message" in up[0] and de and "message_id" in de[0] and ids == list(range(head + 1, head + 1 + len(ids))), [e["type"] for e in ev])
http("POST", f"/api/channels/{ch}/archive", t0)
a = http("PATCH", f"/api/messages/{q2}", t1, {"body": "x"}); b = http("DELETE", f"/api/messages/{q2}", t1)
http("POST", f"/api/channels/{ch}/unarchive", t0)
check("E1.11", "edit/delete in archived channel -> 423", (a[0], b[0]) == (423, 423), (a[0], b[0]))
finish()
PY
