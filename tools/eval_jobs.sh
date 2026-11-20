#!/bin/bash
# tools/eval_jobs.sh : evaluate every job in runlogs/index.txt that has no eval yet -> runlogs/eval/<job>.txt
ROOT=$(cd "$(dirname "$0")/.." && pwd); mkdir -p "$ROOT/runlogs/eval"
C="c_1 c_2 c_3 c_4 c_5"; E="e_1 e_2 e_3 e_4 e_5"; D="d_1 d_2 d_3 d_4"
grep -E '^[0-9]' "$ROOT/runlogs/index.txt" | while read run job _; do
  out="$ROOT/runlogs/eval/$(basename $job).txt"; [ -s "$out" ] && continue
  case $run in 1.1|2.1) l=$C;; 1.2|2.2) l=$D;; 1.3|2.3) l=$E;; 3|4) l="$C $E $D";; *) l="$C $E $D regression";; esac
  a=$(ls -d "$ROOT/tasks/slack-clone-course/$job"/*/artifacts/app); [ -n "$(ls "$a")" ] || continue
  echo "$a|$out|$l"
done | while IFS="|" read a o l; do
  bash "$ROOT/tools/eval_app.sh" "$a" "$o" "$l" >/dev/null &
  while [ $(jobs -r | wc -l) -ge ${P:-3} ]; do sleep 2; done
done; wait
