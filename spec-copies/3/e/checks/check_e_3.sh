#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0, u1), slug, ws, ch = setup(2)
(t0, i0), (t1, i1) = u0, u1
cname = "c" + S; chan = f"#{slug}/{cname}"; n = "u0x" + S
try:
    w = WS(8001, t1); head = (w.subscribe(ch) or {}).get("head_event_id", -1)
    a = IRC(t0, n); a.read("422", 4)
    a.send(f"JOIN {chan}"); ls = a.read("366", 3)
    j = [i for i, l in enumerate(ls) if " JOIN " in l and chan in l]; k = [i for i, l in enumerate(ls) if code([l], "353")]; e = [i for i, l in enumerate(ls) if code([l], "366")]
    check("E3.1", "JOIN echo, then 353, then 366", j and k and e and j[0] < k[0] < e[0], ls)
    a.send(f"JOIN #{slug}-{cname}"); ls = a.read("403", 3)
    check("E3.2", "hyphenated/unknown channel -> 403", code(ls, "403"), ls)
    a.send(f"PRIVMSG {chan} :hello from irc"); time.sleep(0.5)
    h = http("GET", f"/api/channels/{ch}/messages", t1)[1].get("messages", [])
    check("E3.3", "IRC PRIVMSG stored as a message by the IRC user", any(m.get("body") == "hello from irc" and m.get("author_id") == i0 for m in h), [m.get("body") for m in h][:3])
    post(t1, ch, "hi irc", port=8002)
    ev = msgs(w.drain(0.8, 3)); ids = [f["event_id"] for f in ev]
    check("E3.4", "IRC + web writes share a dense event_id stream", len(ids) == 2 and ids[0] > head > -1 and ids[1] == ids[0] + 1, (head, ids))
    ls = a.read("hi irc", 3)
    check("E3.5", "web post fans out to IRC as :nick!user@host PRIVMSG", any(l.startswith(f":u1x{S}!") and f"PRIVMSG {chan} :hi irc" in l for l in ls), ls)
    check("E3.5b", "no echo of own PRIVMSG", not any("hello from irc" in l for l in ls), ls)
    for cmd in (f"NAMES {chan}", f"WHO {chan}", "LIST", f"MODE {chan}"): a.send(cmd)
    a.send(f"TOPIC {chan}"); ls = a.read(None, 1.5)
    check("E3.6", "NAMES/WHO/LIST/MODE/TOPIC accepted; TOPIC -> 331/332", not code(ls, "421") and (code(ls, "331") or code(ls, "332")), ls)
    kill_port(8000); time.sleep(0.3)
    post(t1, ch, "after kill", port=8001); ls = a.read("after kill", 3)
    a.send(f"PRIVMSG {chan} :irc after kill"); time.sleep(0.6)
    h = http("GET", f"/api/channels/{ch}/messages", t1, port=8002)[1].get("messages", [])
    check("E3.7", "IRC bridge survives a killed HTTP node (both directions)", any("after kill" in l for l in ls) and any(m.get("body") == "irc after kill" for m in h), (ls[-2:], [m.get("body") for m in h][:2]))
    wait_health(8000, 8)
except OSError as e:
    bad("E3.0", "IRC gateway reachable on 6667", e)
finish()
PY
