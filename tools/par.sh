#!/bin/bash
# tools/par.sh <run> <k> : one run in its own task copy, so several can run at once
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); RUN=$1; ID="r$(echo $1 | tr . _)-$2"; T="$ROOT/tasks/slack-clone-course"
P="$T/par/$ID"
[ -e "$P" ] || rsync -a --exclude /environment/spec "$T/course-staff-files/final_task/" "$P/"
[ -d "$P/environment/spec" ] || python3 "$ROOT/tools/make_spec.py" "$RUN" "$P/environment/spec" >/dev/null || exit 1
sed -i '' "s|^name = .*|name = \"abundant/slack-clone-$(echo $ID | tr _ -)\"|" "$P/task.toml"
JOB="$(date +%Y-%m-%d__%H-%M-%S)_$ID"; LOG="$ROOT/runlogs/$RUN-$ID.log"
cd "$ROOT"; set -a; source dev.env; set +a
uv run harbor run -p "$P" -y --agent mini_swe_agent_local:MiniSweAgentLocal --model openai/gpt-5.6-luna -e docker \
  -o "$T/jobs" --job-name "$JOB" --disable-verification --agent-setup-timeout-multiplier 5 --agent-kwarg cost_limit=3 --env-file dev.env \
  --agent-kwarg config_file="$T/course-staff-files/final_task/agent_llm_config/mswea-openai-gpt-5.6-luna-no-reasoning.yaml" > "$LOG" 2>&1
cost=$(python3 -c "import json,glob;print(round(json.load(open(glob.glob('$T/jobs/$JOB/*/result.json')[0]))['agent_result']['cost_usd'],3))" 2>/dev/null)
echo "$RUN jobs/$JOB \$${cost:-?} $(basename "$LOG")" >> "$ROOT/runlogs/index.txt"
