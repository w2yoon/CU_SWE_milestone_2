#!/usr/bin/env python3
"""Summarize one job folder from its trajectory: python3 tools/summarize.py <job_dir> [--json]"""
import glob, json, re, sys

CHK = re.compile(r"bash\s+/spec/\S*?(check_[a-e]_\d|regression)\.sh")
RES = re.compile(r"RESULT: (\d+) passed, (\d+) failed|REGRESSION: (\d+) broken")


def summarize(job):
    trial = glob.glob(f"{job}/*/agent/mini-swe-agent.trajectory.json")[0]
    msgs = json.load(open(trial))["messages"]
    res = json.load(open(glob.glob(f"{job}/*/result.json")[0]))
    runs, last, submitted, steps = 0, {}, False, 0
    for i, m in enumerate(msgs):
        if m["role"] != "assistant": continue
        steps += 1
        cmd = "".join(re.findall(r"```mswea_bash_command\n(.*?)\n```", m.get("content") or "", re.S))
        if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in cmd: submitted = True
        names = CHK.findall(cmd)
        if not names or i + 1 >= len(msgs): continue
        runs += 1
        outs = RES.findall(msgs[i + 1].get("content") or "")
        for n, o in zip(names, outs):
            last[n] = f"{o[2]} broken" if o[2] else f"{o[0]}/{int(o[0]) + int(o[1])}"
    failing = [n for n, v in last.items() if (v.endswith("broken") and not v.startswith("0 ")) or (not v.endswith("broken") and v.split("/")[0] != v.split("/")[1])]
    ar = res.get("agent_result") or {}
    return {"job": job.rstrip("/").split("/")[-1], "cost": round(ar.get("cost_usd") or 0, 3), "steps": steps,
            "check_runs": runs, "submitted": submitted, "submitted_with_failures": submitted and bool(failing),
            "failing_at_end": failing, "last": last, "exception": bool(res.get("exception_info")),
            "minutes": None}


if __name__ == "__main__":
    s = summarize(sys.argv[1])
    print(json.dumps(s) if "--json" in sys.argv else "\n".join(f"{k}: {v}" for k, v in s.items()))
