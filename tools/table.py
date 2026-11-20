#!/usr/bin/env python3
"""Print the 22-row results table from runlogs/index.txt, runlogs/eval/ and the trajectories."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__)); from summarize import summarize
J = "tasks/slack-clone-course/"; COMP = {"1.1": "C", "1.2": "D", "1.3": "E", "2.1": "C", "2.2": "D", "2.3": "E", "3": "C, D, E", "4": "C, D, E"}

def scores(path):
    s, cur = {}, None
    for f in (path, path.replace(".txt", ".e45.txt")):
        if not os.path.exists(f): continue
        for l in open(f):
            if l.startswith("=== "): cur = l.split()[1]
            elif m := re.match(r"RESULT: (\d+) passed, (\d+) failed", l): s[cur] = (int(m[1]), int(m[2]))
            elif m := re.match(r"REGRESSION: (\d+) broken", l): s[cur] = int(m[1])
    return s

def fmt(s):
    out = []
    for c in "cedab":
        v = [x for k, x in s.items() if k[0] == c and k != "regression"]
        if v: out.append(f"{c.upper()}: {sum(p for p, _ in v)} passed, {sum(f for _, f in v)} failed")
    if "regression" in s: out.append(f"A and B: {s['regression']} broken")
    return "; ".join(out)

seen, rows = {}, sorted((l.split()[:3] for l in open("runlogs/index.txt") if l[0].isdigit()), key=lambda r: (float(r[0]), r[1]))
for run, job, cost in rows:
    seen[run] = seen.get(run, 0) + 1
    t = summarize(J + job); s = scores(f"runlogs/eval/{os.path.basename(job)}.txt")
    res = fmt(s) if s else "(no app snapshot) in-run: " + fmt({k[6:]: tuple(map(int, (v.split('/')[0], int(v.split('/')[1]) - int(v.split('/')[0])))) for k, v in t["last"].items()})
    sub = "not submitted (time/cost cap)" if not t["submitted"] else ("yes" if t["submitted_with_failures"] else "no")
    print(f"| {run} | {COMP.get(run, 'A to E')} | {seen[run]} | `{job}` | {res} | {cost} | {t['check_runs']} | {sub} | |")
