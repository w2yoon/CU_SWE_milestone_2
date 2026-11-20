# Slack clone — milestone 2: read this first

Read this whole file first, then the files it names. `/spec` is read-only: never edit, move or delete anything in `/spec`, including the checks. If a check fails, fix the code, not the check. `/app` is where your code goes. The task description you were given is still the grading contract; `/spec` tells you what to build now, in what order, and how to prove it works.

## Scope

Build the whole app: components A (roles and invitations) and B (channels and message history) from their spec files, then C (features built on messages), E (IRC, web UI and message edits), D (cluster robustness), stage by stage. The IRC gateway is its own process (`irc.py`, started by the supervisor), so a killed HTTP node never stops IRC; that is checked in E.3.

Anything in the task description that is not named in this file or in the files below is out of scope for this run: do not spend time on it.

## Architecture (required)

`/app` starts **empty** (only `timer.sh`, which you must leave alone). Create, in `/app`:

- `start.sh` (executable): `exec python3 /app/supervisor.py`. It must stay in the foreground.
- `supervisor.py`: writes its PID to `/tmp/supervisor.pid`; starts three copies of `/app/server.py` with env `NODE_ID`=0/1/2 and `PORT`=8000/8001/8002, plus `redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no`, plus (once you build an IRC gateway) `python3 /app/irc.py`; every child gets `start_new_session=True` and a log `/tmp/huddle-<name>.log`; every ≤ 0.5 s it restarts any child that exited; on SIGTERM/SIGINT it kills its children by their saved PIDs and exits.
- `server.py`: Python standard library only (`http.server.ThreadingHTTPServer` with `daemon_threads = True`, bound to `127.0.0.1:$PORT`; `sqlite3`). All processes use ONE shared SQLite file `/app/huddle.db` (WAL, `busy_timeout` 5000). Put shared code (db, auth, messages, events) in modules next to it, e.g. `core.py`, so `irc.py` can reuse it. Keep `/app/requirements.txt` (it may be empty).
- Cross-node delivery: every durable channel change runs in ONE `BEGIN IMMEDIATE` transaction that changes the data, bumps `channel_seq.last_event_id` by 1 and inserts the row into `events(channel_id, event_id, type, payload_json)`. Every WebSocket thread and the IRC gateway poll `events` (every 50 ms) and forward new rows. No process keeps shared state in memory; Redis is not on the delivery path.

## How to run things (every command is killed after 30 s — never run a server in the foreground)

```bash
kill $(cat /tmp/supervisor.pid) 2>/dev/null; sleep 1; (cd /app && nohup ./start.sh >/tmp/cluster.log 2>&1 </dev/null &) >/dev/null 2>&1; sleep 2; curl -s 127.0.0.1:8000/api/health
tail -n 30 /tmp/huddle-0.log        # node 0 stderr; look here on any 500 or crash
```

Restart the cluster this way after every code change, before running a check. Never kill processes by name (`pkill`, `killall`, `pgrep -f`, `ps | grep`): that can kill you. Kill only by a saved PID or the PID that owns a port (`ss -ltnp 'sport = :8001'`). If a port stays busy after a restart, find its owner with `ss -ltnp` and kill that PID.

Each check starts the cluster itself if it is not answering, prints its test results, ends with a line like `RESULT: 12 passed, 0 failed`, and finishes in under 30 s. Run each check as its own command. Check the time left with `bash /app/timer.sh`.

## Common mistakes (avoid)

- Order of errors on every route: unauthenticated → 401 first, unknown id/slug → 404, permission → 403, body validation → 400.
- Wrong envelope key (`{"member":...}`, `{"replies":...}`, `{"reactions":...}`, `{"group":...}`, `{"file":...}`).
- Any state in Python globals: nodes are separate processes and any of them may be killed at any moment.
- Read-then-write races on counters (event ids, invitation uses): one `BEGIN IMMEDIATE` transaction, test `rowcount`.
- A handler exception must return a JSON error (`400` for bad input, `500` only for real bugs), never drop the connection; non-numeric path ids → `404`, non-object JSON bodies → `400`.
- Schema without `IF NOT EXISTS` (all processes start at once).
- Writing one huge file in one command: write `server.py` in several smaller steps (`cat > ... <<'EOF'` then `cat >> ...`), and run `python3 -m py_compile` after each.

## The A and B regression suite

`bash /spec/checks/regression.sh` re-tests every rule of components A (auth, workspaces, roles, invitations) and B (channels, messages, pagination, pins, search, mentions, read state, durable WebSocket events), plus the cluster baseline. It prints one `REGRESSION FAIL ...` line per broken rule, then a total such as `REGRESSION: 0 broken`. The task description gives the exact shape of every route it tests.

## Files

| Stage | Spec | Check |
|---|---|---|
| A.1 | `/spec/a/a-1.md` (auth, workspaces, members, open join, cluster baseline) | `bash /spec/checks/regression.sh` |
| A.2 | `/spec/a/a-2.md` (roles, ownership transfer, channel permissions) | `bash /spec/checks/regression.sh` |
| A.3 | `/spec/a/a-3.md` (invitations, join mode) | `bash /spec/checks/regression.sh` |
| B.1 | `/spec/b/b-1.md` (channel management) | `bash /spec/checks/regression.sh` |
| B.2 | `/spec/b/b-2.md` (messages, pagination, replies, pins, search) | `bash /spec/checks/regression.sh` |
| B.3 | `/spec/b/b-3.md` (mentions, read state) | `bash /spec/checks/regression.sh` |
| B.4 | `/spec/b/b-4.md` (durable events, WebSocket, cluster fan-out) | `bash /spec/checks/regression.sh` |
| C.1 | `/spec/c/c-1.md` (reactions, DMs) | `bash /spec/c/checks/check_c_1.sh` |
| C.2 | `/spec/c/c-2.md` (files) | `bash /spec/c/checks/check_c_2.sh` |
| C.3 | `/spec/c/c-3.md` (slash commands) | `bash /spec/c/checks/check_c_3.sh` |
| C.4 | `/spec/c/c-4.md` (user groups, @group, @here) | `bash /spec/c/checks/check_c_4.sh` |
| C.5 | `/spec/c/c-5.md` (profiles, user.updated) | `bash /spec/c/checks/check_c_5.sh` |
| E.1 | `/spec/e/e-1.md` (message edit and delete) | `bash /spec/e/checks/check_e_1.sh` |
| E.2 | `/spec/e/e-2.md` (IRC registration) | `bash /spec/e/checks/check_e_2.sh` |
| E.3 | `/spec/e/e-3.md` (IRC channels and web bridge) | `bash /spec/e/checks/check_e_3.sh` |
| E.4 | `/spec/e/e-4.md` (SPA shell, auth, workspace, channels) | `bash /spec/e/checks/check_e_4.sh` |
| E.5 | `/spec/e/e-5.md` (SPA messages, threads, reactions) | `bash /spec/e/checks/check_e_5.sh` |
| D.1 | `/spec/d/d-1.md` (supervisor, Redis, killed HTTP node) | `bash /spec/d/checks/check_d_1.sh` |
| D.2 | `/spec/d/d-2.md` (WebSocket across a killed node) | `bash /spec/d/checks/check_d_2.sh` |
| D.3 | `/spec/d/d-3.md` (killed Redis) | `bash /spec/d/checks/check_d_3.sh` |
| D.4 | `/spec/d/d-4.md` (WebSocket speed under load) | `bash /spec/d/checks/check_d_4.sh` |

Shared helpers used by the checks (do not edit): `/spec/c/checks/lib.sh`, `/spec/c/checks/chk.py`, `/spec/e/checks/lib.sh`, `/spec/e/checks/chk.py`, `/spec/d/checks/lib.sh`, `/spec/d/checks/chk.py`.
Regression suite for A and B: `bash /spec/checks/regression.sh`.

## Order of work (strict)

1. Create the architecture above. Then for each stage A.1 → B.4 in the table: read its spec file → implement only that stage → restart → run `bash /spec/checks/regression.sh` → fix until it prints no `REGRESSION FAIL` line for that stage's rule ids. Before step 2, `regression.sh` must print `REGRESSION: 0 broken`.
2. Then C.1–C.5 → E.1–E.5 → D.1–D.4. For each stage: read its spec file → implement only that stage → restart → run its check → fix until `0 failed` → run `bash /spec/checks/regression.sh` and fix anything it reports before moving on → next stage. If one rule still fails after three fix attempts, move on and come back to it at the end.
3. At the end, restart from cold and run every check once more plus `regression.sh`.

## Before you submit

You may run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when all of these are true, from output you actually saw in this session, against a freshly restarted cluster:

- every check in the Files table prints `RESULT: N passed, 0 failed`;
- the last line of `bash /spec/checks/regression.sh` is `REGRESSION: 0 broken`;
- `./start.sh` boots cleanly from a cold start (stop everything, start, wait 3 s, all three health endpoints answer `200`).

If some checks still fail near the time limit, submit the state with the fewest failures that still boots cleanly. Never trade a booting cluster for a feature.

Time guide (50 min total; check with `bash /app/timer.sh`): A and B 14 min, C 10, E 13, D 6, final re-run of every check 5.
