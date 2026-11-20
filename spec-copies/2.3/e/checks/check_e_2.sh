#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
def nums(ls): return [l.split()[1] for l in ls if len(l.split()) > 1 and l.split()[1].isdigit()]
try:
    t, _ = reg("irc" + S); n = "n" + S
    a = IRC(t, n); ls = a.read("422", 4); ns = nums(ls)
    check("E2.1", "registration -> 001..005 then 422", ns[:6] == ["001", "002", "003", "004", "005", "422"], ns[:8])
    b = IRC("badtoken", "x" + S); ls = b.read("464", 3)
    c = IRC(None, "y" + S); ls2 = c.read("464", 3)
    check("E2.2", "bad or missing PASS -> 464", code(ls, "464") and code(ls2, "464"), (ls[:2], ls2[:2]))
    t2, _ = reg("irc2" + S); d = IRC(t2, n); ls = d.read("433", 3)
    d.send(f"NICK {n}b"); d.send(f"USER {n}b 0 * :x"); ls2 = d.read("001", 3)
    check("E2.3", "nick collision -> 433, connection stays, retry registers", code(ls, "433") and code(ls2, "001"), (ls[:2], ls2[:2]))
    a.send("PING :abc"); ls = a.read("PONG", 3)
    check("E2.4", "PING -> PONG :abc", any("PONG" in l and "abc" in l for l in ls), ls)
    a.send("FOOBAR"); ls = a.read("421", 3)
    check("E2.5", "unknown command -> 421", code(ls, "421") and "FOOBAR" in " ".join(ls), ls)
    for cmd in ("PONG :x", f"MODE {n}", "WHO", "LIST", "NAMES", "TOPIC"): a.send(cmd)
    a.send("PING :end"); ls = a.read("end", 3)
    check("E2.7", "PONG/MODE/WHO/LIST/NAMES/TOPIC accepted (no 421)", not code(ls, "421") and any("end" in l for l in ls), ls)
    d.send("QUIT :bye"); ls = d.read(None, 2)
    check("E2.6", "QUIT closes without error numeric", "<closed>" in ls and not any(len(l.split()) > 1 and l.split()[1][:1] in "45" and l.split()[1].isdigit() for l in ls), ls)
except OSError as e:
    bad("E2.0", "IRC gateway reachable on 6667", e)
finish()
PY
