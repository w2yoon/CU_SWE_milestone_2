#!/usr/bin/env python3
"""Assemble the /spec folder of one run: python3 tools/make_spec.py <run> [outdir]
Writes milestone-2-specs/ (what the agent gets) and a copy in spec-copies/<run>/."""
import re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "spec-src"
LIVE = ROOT / "tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-2-specs"

COMP = {
    "a": ("A", "roles and invitations", ["auth, workspaces, members, open join, cluster baseline", "roles, ownership transfer, channel permissions", "invitations, join mode"]),
    "b": ("B", "channels and message history", ["channel management", "messages, pagination, replies, pins, search", "mentions, read state", "durable events, WebSocket, cluster fan-out"]),
    "c": ("C", "features built on messages", ["reactions, DMs", "files", "slash commands", "user groups, @group, @here", "profiles, user.updated"]),
    "d": ("D", "cluster robustness", ["supervisor, Redis, killed HTTP node", "WebSocket across a killed node", "killed Redis", "WebSocket speed under load"]),
    "e": ("E", "IRC, web UI and message edits", ["message edit and delete", "IRC registration", "IRC channels and web bridge", "SPA shell, auth, workspace, channels", "SPA messages, threads, reactions"]),
}
CDE = ["c", "e", "d"]
RUNS = {
    "1.1": (["c"], "score_only", "basics"), "1.2": (["d"], "score_only", "basics"), "1.3": (["e"], "score_only", "basics"),
    "2.1": (["c"], "verbose", "basics"), "2.2": (["d"], "verbose", "basics"), "2.3": (["e"], "verbose", "basics"),
    "3": (CDE, "verbose", "basics"), "4": (CDE, "verbose", "checks-only"),
    "eval": (CDE, "verbose", "plain"),
    "5.1": (CDE, "verbose", "regression"), "5.2": (CDE, "verbose", "regression+spec"), "5.3": (CDE, "verbose", "plain"),
}
TIME = {1: "basics 10 min, then about 7 min per stage, final re-run of every check 5 min.",
        3: "basics 8 min, C 12, E 14, D 8, final re-run of every check 5.",
        5: "A and B 14 min, C 10, E 13, D 6, final re-run of every check 5."}


def steps(c):
    return [(f"{COMP[c][0]}.{i}", f"/spec/{c}/{c}-{i}.md", f"bash /spec/{c}/checks/check_{c}_{i}.sh", d) for i, d in enumerate(COMP[c][2], 1)]


def copy_comp(c, out, mode, with_spec, with_checks):
    d = out / c
    if with_spec:
        d.mkdir(parents=True)
        conv = (SRC / "common/conventions.md").read_text()
        for f in sorted((SRC / c).glob("*.md")):
            t = f.read_text().replace("{{CONVENTIONS}}", conv)
            if not with_checks:
                t = re.sub(r"bash /spec/[ab]/checks/check_[ab]_\d\.sh", "bash /spec/checks/regression.sh", t).replace("`check_a_1.sh`", "`regression.sh`")
            (d / f.name).write_text(t)
    if with_checks:
        shutil.copytree(SRC / c / "checks", d / "checks")
        if c in "cde": shutil.copy(ROOT / "tools/chk.py", d / "checks/chk.py")
        lib = d / "checks/lib.sh"
        lib.write_text(re.sub(r"^FEEDBACK=\w+", f"FEEDBACK={mode}", lib.read_text(), flags=re.M))


def regression(out):
    lib = (SRC / "a/checks/lib.sh").read_text()
    lib = lib.replace("FEEDBACK=verbose\n", "").rsplit("\nboot", 1)[0]
    lib = re.sub(r"^ok\(\).*$", "ok()  { PASS=$((PASS+1)); return 0; }", lib, flags=re.M)
    lib = re.sub(r"^bad\(\).*$", 'bad() { FAIL=$((FAIL+1)); [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL $SEC $1 $2: $3"; return 0; }', lib, flags=re.M)
    lib = re.sub(r"^summary\(\).*$", 'summary() { echo "__BROKEN $FAIL"; }', lib, flags=re.M)
    lib = lib.replace('echo "RESULT: 0 passed, 1 failed"; exit 1', 'echo "REGRESSION: 1 broken"; exit 1').replace('echo "FAIL 0 cluster', 'echo "REGRESSION FAIL cluster')
    secs, base = [], ""
    for c, name in (("a", "roles"), ("b", "channels")):
        for f in sorted((SRC / c / "checks").glob("check_*.sh")):
            body = f.read_text().split("\n", 2)[2].rsplit("summary", 1)[0]
            if "# --- milestone 0 baseline" in body:
                body, base = body.split("# --- milestone 0 baseline", 1)
                base = "# --- milestone 0 baseline" + base
            body = body.replace('"$(dirname "$0")/events.py"', "/tmp/regression_events.py")
            secs.append(f"sec_{f.stem[6:]}() {{\nSEC={name}\n{body.strip()}\n}}")
    ev = (SRC / "b/checks/events.py").read_text()
    names = " ".join(s.split("(")[0][4:] for s in secs)
    text = f"""#!/bin/bash
# A and B regression suite: re-tests every A and B rule, prints only what broke, then REGRESSION: N broken.
FEEDBACK=verbose
{lib}
cat > /tmp/regression_events.py <<'PYEOF'
{ev}PYEOF
{chr(10).join(secs)}
boot
R=/tmp/regression.$$; rm -f $R.*
for s in {names}; do ( SECONDS=0; sec_$s; summary ) > $R.$s 2>&1 & done; wait
( SEC=baseline; SECONDS=0; {base.strip()}
summary ) > $R.zz 2>&1
grep -h '^REGRESSION FAIL' $R.* ; N=$(cat $R.* | grep '^__BROKEN' | awk '{{s+=$2}} END {{print s+0}}')
for s in {names} zz; do grep -q '^__BROKEN' $R.$s || {{ N=$((N+1)); [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL $s section did not finish"; }}; done
rm -f $R.*
echo "REGRESSION: $N broken"; [ "$N" -eq 0 ]
"""
    (out / "checks").mkdir(exist_ok=True)
    (out / "checks/regression.sh").write_text(text)


def files_table(comps, spec_files):
    if spec_files:
        rows = ["| Stage | Spec | Check |", "|---|---|---|"] + [f"| {s} | `{p}` ({d}) | `{k}` |" for c in comps for s, p, k, d in steps(c)]
    else:
        rows = ["| Stage | Check (read it: it is the specification) |", "|---|---|"] + [f"| {s} ({d}) | `{k}` |" for c in comps for s, p, k, d in steps(c)]
    shared = [f"`/spec/{c}/checks/lib.sh`" + (f", `/spec/{c}/checks/chk.py`" if c in "cde" else "") + (", `/spec/b/checks/events.py`" if c == "b" else "") for c in comps]
    return "\n".join(rows) + "\n\nShared helpers used by the checks (do not edit): " + ", ".join(shared) + "."


def build(run, out):
    comps, mode, kind = RUNS[run]
    if out.exists():
        for p in out.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    ab = {"regression": [], "regression+spec": ["a", "b"], "plain": ["a", "b"]}.get(kind, [])
    for c in ab: copy_comp(c, out, mode, True, kind == "plain")
    for c in comps: copy_comp(c, out, mode, kind != "checks-only", True)
    if kind.startswith("regression") or run == "eval": regression(out)
    names = ", ".join(f"{COMP[c][0]} ({COMP[c][1]})" for c in comps)
    scope = {"basics": f"Build the Basics below, then component{'s' if len(comps) > 1 else ''} {names}, stage by stage.",
             "checks-only": f"Build the Basics below, then component{'s' if len(comps) > 1 else ''} {names}, stage by stage. There are no spec files for them: the check scripts are the specification.",
             "regression": f"Build the whole app: components A and B as the task description defines them (until the regression suite passes), then {names}, stage by stage.",
             "regression+spec": f"Build the whole app: components A (roles and invitations) and B (channels and message history) from their spec files, then {names}, stage by stage.",
             "plain": f"Build the whole app: components A (roles and invitations) and B (channels and message history), then {names}, stage by stage."}[kind]
    notes = []
    if "e" in comps: notes.append("The IRC gateway is its own process (`irc.py`, started by the supervisor), so a killed HTTP node never stops IRC; that is checked in E.3.")
    if "d" in comps and "e" not in comps: notes.append("Component E (IRC) is not built in this run, so D does not test IRC.")
    if "e" in comps and "c" not in comps: notes.append("The web UI (E.5) needs the two reaction routes of component C; build just those inside E.5 as it describes.")
    if notes: scope += " " + " ".join(notes)
    allc = ab + comps if kind == "plain" else comps
    order = []
    if kind in ("basics", "checks-only"):
        order.append("1. Create `start.sh`, `supervisor.py` and `server.py` with the Basics above, restart, and confirm `/api/health` on 8000, 8001 and 8002.")
    elif kind == "regression":
        order.append("1. Create the architecture above, then build components A and B as the task description defines them. Run `bash /spec/checks/regression.sh` and fix the code until it prints `REGRESSION: 0 broken`.")
    elif kind == "regression+spec":
        order.append("1. Create the architecture above. Then for each stage A.1 → B.4 in the table: read its spec file → implement only that stage → restart → run `bash /spec/checks/regression.sh` → fix until it prints no `REGRESSION FAIL` line for that stage's rule ids. Before step 2, `regression.sh` must print `REGRESSION: 0 broken`.")
    else:
        order.append("1. Create the architecture above. Then for each stage A.1 → B.4 in the table: read its spec file → implement only that stage → restart → run that stage's check → fix until `0 failed` → rerun every earlier A and B check and fix anything that broke.")
    read = "read its check script (it shows every request and the expected result)" if kind == "checks-only" else "read its spec file"
    after = {"regression": "run `bash /spec/checks/regression.sh` and fix anything it reports before moving on",
             "regression+spec": "run `bash /spec/checks/regression.sh` and fix anything it reports before moving on",
             "plain": "rerun every A and B check (`check_a_1.sh` … `check_b_4.sh`, one command each) and fix anything that broke before moving on"}.get(kind, "rerun every earlier check of this run and fix anything that broke")
    stages = " → ".join(f"{COMP[c][0]}.1–{COMP[c][0]}.{len(COMP[c][2])}" for c in comps)
    order.append(f"2. Then {stages}. For each stage: {read} → implement only that stage → restart → run its check → fix until `0 failed` → {after} → next stage. If one rule still fails after three fix attempts, move on and come back to it at the end.")
    order.append("3. At the end, restart from cold and run every check once more" + (" plus `regression.sh`." if kind.startswith("regression") else "."))
    gate = [f"- every check in the Files table prints `RESULT: N passed, 0 failed`;"]
    if kind.startswith("regression"): gate.append("- the last line of `bash /spec/checks/regression.sh` is `REGRESSION: 0 broken`;")
    tmpl = (SRC / "README.tmpl.md").read_text()
    keep = {"basics": kind in ("basics", "checks-only"), "regression": kind.startswith("regression")}
    tmpl = re.sub(r"<!--IF:(\w+)-->\n(.*?)<!--END-->\n", lambda m: m.group(2) if keep[m.group(1)] else "", tmpl, flags=re.S)
    files = files_table(allc if kind != "regression+spec" else ["a", "b"] + comps, kind != "checks-only")
    if kind == "regression+spec":
        files = files.replace("| Check |", "| Check |", 1)
        for c in ("a", "b"):
            for s, p, k, d in steps(c): files = files.replace(f"`{k}`", "`bash /spec/checks/regression.sh`")
        files = files.replace("`/spec/a/checks/lib.sh`, `/spec/b/checks/lib.sh`, `/spec/b/checks/events.py`, ", "")
    if kind.startswith("regression"): files += "\nRegression suite for A and B: `bash /spec/checks/regression.sh`."
    t = len(comps) == 1 and 1 or (5 if kind in ("regression", "regression+spec", "plain") else 3)
    text = (tmpl.replace("{{SCOPE}}", scope).replace("{{FILES}}", files).replace("{{ORDER}}", "\n".join(order))
            .replace("{{GATE}}", "\n".join(gate)).replace("{{TIME}}", TIME[t]))
    (out / "README.md").write_text(text)
    for f in out.rglob("*.sh"): f.chmod(0o755)


if __name__ == "__main__":
    run = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else LIVE
    build(run, out)
    if len(sys.argv) == 2:
        cp = ROOT / "spec-copies" / run
        if cp.exists(): shutil.rmtree(cp)
        shutil.copytree(out, cp)
    print(f"built run {run} -> {out}")
