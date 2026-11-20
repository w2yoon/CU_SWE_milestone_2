# Milestone 2 report

- Name: Seoyoon Jeong
- UNI: sj3477

All results are from my own checks. I re-ran every check on each job's saved `artifacts/app` in a fresh container (`tools/eval_app.sh`). For the two jobs where harbor did not save `/app`, I use the last check output the agent saw. Total cost of the 22 runs: $35.36.

Note: to finish on time, Run 1.3 repeat 2 and Runs 3 to 5.3 ran 11 at once on one laptop. Checks were slower under that load, and the browser checks (E4, E5) often hit the 27 s limit while the agent worked. This likely lowers E scores in those runs.

## Runs

"Check results" counts rules passed and failed per component. "Ran checks" is how many commands ran a check script.

| Run | Component | Repeat | Job folder | Check results | Cost | Ran checks (times) | Submitted with failures? | Problems |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | C | 1 | `jobs/2026-11-13__19-39-39` | C: 50 passed, 2 failed | $1.424 | 46 | no | C4 @group rules broke after its last check |
| 1.1 | C | 2 | `jobs/2026-11-13__19-56-14` | C: 52 passed, 0 failed | $0.649 | 19 | no | |
| 1.2 | D | 1 | `jobs/2026-11-13__20-04-13` | D: 21 passed, 1 failed | $0.167 | 9 | no | D4 latency rule failed at eval |
| 1.2 | D | 2 | `jobs/2026-11-13__20-08-43` | in-run: D: 22 passed, 0 failed | $0.109 | 8 | no | harbor failed to save `/app`; score is from the last in-run checks |
| 1.3 | E | 1 | `jobs/2026-11-13__20-13-59` | E: 46 passed, 0 failed | $0.805 | 30 | no | |
| 1.3 | E | 2 | `jobs/2026-11-13__21-35-42_r1_3-9` | E: 42 passed, 4 failed | $3.018 | 60 | not submitted (time/cost cap) | hit the 50 min cap; ran in parallel |
| 2.1 | C | 1 | `jobs/2026-11-13__20-29-23` | C: 52 passed, 0 failed | $0.27 | 18 | no | |
| 2.1 | C | 2 | `jobs/2026-11-13__20-39-22` | C: 52 passed, 0 failed | $0.579 | 27 | no | |
| 2.2 | D | 1 | `jobs/2026-11-13__20-49-24` | D: 22 passed, 0 failed | $0.157 | 10 | no | |
| 2.2 | D | 2 | `jobs/2026-11-13__20-53-58` | D: 22 passed, 0 failed | $0.073 | 6 | no | |
| 2.3 | E | 1 | `jobs/2026-11-13__20-58-09` | E: 46 passed, 0 failed | $1.483 | 48 | no | |
| 2.3 | E | 2 | `jobs/2026-11-13__21-16-09` | E: 45 passed, 1 failed | $2.209 | 64 | yes | submitted with E4 still timing out |
| 3 | C, D, E | 1 | `jobs/2026-11-13__21-46-21_r3-1` | C: 52 passed, 0 failed; E: 28 passed, 18 failed; D: 22 passed, 0 failed | $2.203 | 62 | yes | SPA (E4/E5) mostly not working; ran in parallel |
| 3 | C, D, E | 2 | `jobs/2026-11-13__21-46-51_r3-2` | in-run: C: 50 passed, 2 failed; E: 29 passed, 2 failed; D: 15 passed, 1 failed | $2.349 | 58 | yes | harbor failed to save `/app`; in-run scores are partial |
| 4 | C, D, E | 1 | `jobs/2026-11-13__21-47-21_r4-1` | C: 52 passed, 0 failed; E: 46 passed, 0 failed; D: 22 passed, 0 failed | $3.009 | 74 | not submitted (time/cost cap) | hit the 50 min cap, but all checks pass |
| 4 | C, D, E | 2 | `jobs/2026-11-13__21-47-51_r4-2` | C: 49 passed, 3 failed; E: 33 passed, 13 failed; D: 22 passed, 0 failed | $2.328 | 49 | yes | SPA messages (E5) not working |
| 5.1 | A to E | 1 | `jobs/2026-11-13__21-48-21_r5_1-1` | C: 41 passed, 11 failed; E: 34 passed, 12 failed; D: 21 passed, 1 failed; A and B: 76 broken | $2.301 | 44 | yes | last edits broke PATCH routes (501) |
| 5.1 | A to E | 2 | `jobs/2026-11-13__21-48-51_r5_1-2` | C: 52 passed, 0 failed; E: 45 passed, 1 failed; D: 22 passed, 0 failed; A and B: 0 broken | $2.926 | 67 | yes | |
| 5.2 | A to E | 1 | `jobs/2026-11-13__21-49-21_r5_2-1` | C: 21 passed, 31 failed; E: 16 passed, 30 failed; D: 10 passed, 12 failed; A and B: 127 broken | $0.261 | 4 | yes | gave up early: submitted after 4 check runs |
| 5.2 | A to E | 2 | `jobs/2026-11-13__21-49-51_r5_2-2` | C: 49 passed, 3 failed; E: 22 passed, 24 failed; D: 12 passed, 10 failed; A and B: 15 broken | $3.017 | 49 | not submitted (time/cost cap) | hit the cost cap |
| 5.3 | A to E | 1 | `jobs/2026-11-13__21-50-21_r5_3-1` | C: 45 passed, 7 failed; E: 10 passed, 23 failed; D: 14 passed, 8 failed; A and B: 18 broken | $3.024 | 53 | not submitted (time/cost cap) | hit the cap; E2/E3 checks hang (count as 1 fail each) |
| 5.3 | A to E | 2 | `jobs/2026-11-13__21-50-51_r5_3-2` | C: 52 passed, 0 failed; E: 32 passed, 14 failed; D: 20 passed, 2 failed; A and B: 13 broken | $3.003 | 67 | not submitted (time/cost cap) | hit the cap; nested transaction breaks invitations (A6) |

## Experiments

### 1) Feedback

> Do you find that adding detailed output to the checks helps the agent?

#### Runs compared and why

Runs 1.x (`score_only`) against Runs 2.x (`verbose`). Same spec and checks; only the check output differs.

#### Supporting results

| Component | 1.x score_only (rep 1, rep 2) | 2.x verbose (rep 1, rep 2) | Cost 1.x | Cost 2.x |
| --- | --- | --- | --- | --- |
| C | 50/52, 52/52 | 52/52, 52/52 | $1.42, $0.65 | $0.27, $0.58 |
| D | 21/22, 22/22 | 22/22, 22/22 | $0.17, $0.11 | $0.16, $0.07 |
| E | 46/46, 42/46 | 46/46, 45/46 | $0.81, $3.02 | $1.48, $2.21 |

#### Answer

A little. Verbose was equal or better in every pair, and C was cheaper with it. But scores are near full in both modes, so the gap is small.

#### Consistency across repeats

Verbose: 5 of 6 runs fully passed, and the other missed 1 rule. Score-only: 3 of 6 fully passed, and it had the worst repeat (E rep 2, 42/46).

#### Other evidence

With `score_only`, the C agent ran checks 46 times in repeat 1 (vs 18 and 27 with `verbose`). It saw only counts, so it took more tries to find the failing rule.

### 2) Tests as the spec

> Can the checks alone replace the written rules?

#### Runs compared and why

Run 3 (specs + checks) against Run 4 (checks only). Both build C, D and E together in `verbose` mode.

#### Supporting results

| Run | Rep | C | E | D |
| --- | --- | --- | --- | --- |
| 3 (specs + checks) | 1 | 52/52 | 28/46 | 22/22 |
| 3 (specs + checks) | 2 | 50/52 (in-run) | 29/31 (in-run, partial) | 15/16 (in-run, partial) |
| 4 (checks only) | 1 | 52/52 | 46/46 | 22/22 |
| 4 (checks only) | 2 | 49/52 | 33/46 | 22/22 |

#### Answer

Mostly yes, for these components. Without the written specs the agent did as well or better: C and D were the same, and E was better in Run 4. The check output already shows the exact routes, fields and status codes.

#### Consistency across repeats

C and D were steady. E varied a lot in both runs (28 to 46). This comes from the browser part (E4/E5).

#### Other evidence

Run 4 repeat 1 used its whole time budget, but every check passed. Run 3 repeat 2 has no saved `/app`, so its row uses partial in-run numbers.

### 3) Scale

> Do you find that a component passes fewer of its checks when the agent builds it together with other components, rather than on its own?

#### Runs compared and why

Runs 2.x (one component, `verbose`) against Run 3 (C, D, E together, same specs and checks, `verbose`).

#### Supporting results

| Component | Alone, 2.x (rep 1, rep 2) | Together, Run 3 (rep 1, rep 2) | Together, Runs 5.x (6 repeats) |
| --- | --- | --- | --- |
| C | 52/52, 52/52 | 52/52, 50/52 | 41, 52, 21, 49, 45, 52 (of 52) |
| D | 22/22, 22/22 | 22/22, 15/16 | 21, 22, 10, 12, 14, 20 (of 22) |
| E | 46/46, 45/46 | 28/46, 29/31 | 34, 45, 16, 22, 10, 32 (of 46) |

#### Answer

Yes. C and D barely change, but E drops a lot when built with others. E is the biggest part (IRC plus the browser app) and comes after C in the order, so the agent runs short of time.

#### Consistency across repeats

The E drop shows in both Run 3 repeats and in 5 of 6 Run 5.x repeats. C and D drops happen mainly in the bigger 5.x runs.

#### Other evidence

The combined runs ran in parallel, so slower browser checks may add to the E drop (see the note at the top).

### 4) Regression

> a) Do you find that building components C, D, and E breaks features from A and B that were already working?
>
> b) Does it make a difference whether the agent has Specs A and B or only their regression tests?
>
> c) Does it make a difference whether the A and B checks are given as a regression suite or as plain checks?

#### Runs compared and why

- a) Runs 5.1, 5.2 and 5.3 against the milestone 1 app, which passes every A and B check (0 broken).
- b) 5.1 (regression suite only) against 5.2 (regression suite + Specs A and B).
- c) 5.2 (A/B as one regression suite) against 5.3 (A/B as plain staged checks). Both have Specs A and B.

All are scored with the same regression suite at the end.

#### Supporting results

| Run | What the agent had for A/B | A and B broken (rep 1, rep 2) | Submitted? |
| --- | --- | --- | --- |
| Milestone 1 app | (baseline) | 0 | - |
| 5.1 | regression suite | 76, 0 | yes, yes |
| 5.2 | regression suite + specs | 127, 15 | yes (early), no (cap) |
| 5.3 | plain checks + specs | 18, 13 | no (cap), no (cap) |

#### Answer

a) Yes. In 5 of 6 runs, A or B rules that worked before were broken at the end. One example is 5.3 repeat 2, where new C/E code opened a transaction inside another one. Accepting an invitation then failed with `cannot start a transaction within a transaction`.

b) No clear difference. The best repeat (0 broken) had only the suite. 5.2 repeat 1 gave up early (4 check runs, $0.26), so it is an outlier. Without it, 5.1 and 5.2 look about the same.

c) Small or none. 5.2 repeat 2 (15) and 5.3 (18, 13) end close. With plain checks, the agent spent time running seven A/B checks and both 5.3 runs hit the cap.

#### Consistency across repeats

Low. The two repeats differ a lot in 5.1 (76 vs 0) and 5.2 (127 vs 15). With 2 repeats, b) and c) are not firm.

#### Other evidence

In 5.1 repeat 1, the agent's last regression run showed 20 broken, but edits after that broke PATCH routes (501), and the final count is 76.
