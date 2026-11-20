FEEDBACK=score_only
D=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
healthy() { for p in 8000 8001 8002; do curl -fs -m 2 127.0.0.1:$p/api/health >/dev/null || return 1; done; }
boot() {
  healthy && return
  (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 </dev/null &) >/dev/null 2>&1
  for _ in $(seq 20); do healthy && return; sleep 1; done
  [ "$FEEDBACK" = verbose ] && echo "FAIL 0 cluster not answering /api/health on 8000-8002 (see /tmp/start.log)"
  echo "RESULT: 0 passed, 1 failed"; exit 1
}
# py: run the python rules on stdin with the shared helpers in chk.py
py() {
  boot; FEEDBACK=$FEEDBACK PYTHONPATH="$D" timeout -k 1 27 ${PY:-python3} -
  case $? in 124|137) [ "$FEEDBACK" = verbose ] && echo "FAIL T check killed after 27 s (a request or socket hangs)"; echo "RESULT: 0 passed, 1 failed";; esac
}
