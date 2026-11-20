#!/bin/bash
# Rerun checks (default: all of A-E + regression, verbose) on an app folder: tools/eval_app.sh <app_dir> <out_file> ["c_1 c_2 ..."]
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); APP=$(cd "$1" && pwd); OUT=$2
SPEC=$(mktemp -d); python3 "$ROOT/tools/make_spec.py" eval "$SPEC" >/dev/null
N=eval$$; docker run -d --rm --name $N -v "$APP":/app_src:ro -v "$SPEC":/spec:ro slack-clone-course sleep infinity >/dev/null
docker exec $N bash -c 'cp -a /app_src/. /app/ && rm -f /app/huddle.db* && rm -rf /app/data/* && pip install -q -r /app/requirements.txt >/dev/null 2>&1; true'
{
for c in ${3:-a_1 a_2 a_3 b_1 b_2 b_3 b_4 c_1 c_2 c_3 c_4 c_5 e_1 e_2 e_3 e_4 e_5 d_1 d_2 d_3 d_4 regression}; do
  f=/spec/${c:0:1}/checks/check_$c.sh; [ $c = regression ] && f=/spec/checks/regression.sh
  echo "=== $c"; docker exec $N timeout 60 bash $f 2>&1 | tail -40
done
} > "$OUT"
docker rm -f $N >/dev/null; rm -rf "$SPEC"
grep -E '^(=== |RESULT|REGRESSION:)' "$OUT" | paste - - | awk '{print $2, $3, $4, $5, $6, $7}'
