FEEDBACK=verbose
BASE=${BASE:-http://127.0.0.1:8000}
PASS=0; FAIL=0; SECONDS=0; BUDGET=${BUDGET:-24}
uniq() { echo "$BASHPID$RANDOM$RANDOM"; }
pyget() { python3 -c 'import sys,json;d=json.loads(sys.argv[1]);print(eval(sys.argv[2]))' "$1" "$2" 2>/dev/null; }
healthy() { for p in 8000 8001 8002; do curl -fs -m 2 127.0.0.1:$p/api/health >/dev/null || return 1; done; }
boot() {
  healthy && return
  (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 </dev/null &) >/dev/null 2>&1
  for _ in $(seq 20); do healthy && return; sleep 1; done
  [ "$FEEDBACK" = verbose ] && echo "FAIL 0 cluster not answering /api/health on 8000-8002 (see /tmp/start.log)"
  echo "RESULT: 0 passed, 1 failed"; exit 1
}
# req METHOD PATH [TOKEN] [JSON_BODY] [BASE_URL] -> sets CODE and OUT
req() {
  if [ $SECONDS -ge $BUDGET ]; then [ "$FEEDBACK" = verbose ] && echo "ABORT time budget ${BUDGET}s exceeded (a request hangs or crashes)"; FAIL=$((FAIL+1)); summary; exit 1; fi
  local m=$1 p=$2 t=$3 d=$4 b=${5:-$BASE} f=/tmp/_b.$BASHPID
  local a=(-s -m 2 -o $f -w '%{http_code}' -X "$m" "$b$p" -H 'Content-Type: application/json')
  [ -n "$t" ] && a+=(-H "Authorization: Bearer $t")
  [ -n "$d" ] && a+=(-d "$d")
  CODE=$(curl "${a[@]}"); OUT=$(cat $f 2>/dev/null)
}
ok()  { PASS=$((PASS+1)); [ "$FEEDBACK" = verbose ] && echo "PASS $1 $2"; return 0; }
bad() { FAIL=$((FAIL+1)); [ "$FEEDBACK" = verbose ] && echo "FAIL $1 $2: $3"; return 0; }
# expect ID DESC WANT_CODE [python expr on parsed body d]
expect() {
  local good=1
  [ "$CODE" = "$3" ] || good=0
  if [ $good = 1 ] && [ -n "$4" ]; then [ "$(pyget "$OUT" "$4")" = "True" ] || good=0; fi
  if [ $good = 1 ]; then ok "$1" "$2"; else bad "$1" "$2" "expected $3${4:+ and $4}, got $CODE ${OUT:0:100}"; fi
}
reg() {
  req POST /api/auth/register "" "{\"username\":\"$1\",\"password\":\"password123\"}"
  TOK=$(pyget "$OUT" 'd["token"]'); UID_=$(pyget "$OUT" 'd["user"]["id"]')
}
summary() { echo "RESULT: $PASS passed, $FAIL failed"; [ $FAIL -eq 0 ]; }
# mkws: workspace with owner(O) admin(adm) member(mem) guest(gst) outsider(out)
mkws() {
  S=$(uniq); reg "o$S"; OT=$TOK; OID=$UID_; W="w$S"
  req POST /api/workspaces "$OT" "{\"slug\":\"$W\",\"name\":\"WS $S\"}"
  GEN=$(pyget "$OUT" 'd["general_channel"]["id"]'); WID=$(pyget "$OUT" 'd["workspace"]["id"]')
  reg "adm$S"; admT=$TOK; admI=$UID_
  reg "mem$S"; memT=$TOK; memI=$UID_
  reg "gst$S"; gstT=$TOK; gstI=$UID_
  reg "out$S"; outT=$TOK; outI=$UID_
  for t in $admT $memT $gstT; do req POST /api/channels/$GEN/join "$t"; done
  req PATCH /api/workspaces/$W/members/$admI "$OT" '{"role":"admin"}'
  req PATCH /api/workspaces/$W/members/$gstI "$OT" '{"role":"guest"}'
}
mkch() {
  local p=false; [ "$3" = private ] && p=true
  req POST /api/workspaces/$W/channels "$1" "{\"name\":\"$2\",\"is_private\":$p}"
  CH=$(pyget "$OUT" 'd["channel"]["id"]')
}
post() {
  local d="{\"body\":\"$3\"}"; [ -n "$4" ] && d="{\"body\":\"$3\",\"parent_id\":$4}"
  req POST /api/channels/$2/messages "$1" "$d"; MID=$(pyget "$OUT" 'd["message"]["id"]')
}
boot
