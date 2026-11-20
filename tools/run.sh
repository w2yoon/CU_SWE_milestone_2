#!/bin/bash
# tools/run.sh <run> : build that run's /spec, run the agent once, append the job folder to runlogs/index.txt
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); RUN=$1; JOBS="$ROOT/tasks/slack-clone-course/jobs"
python3 "$ROOT/tools/make_spec.py" "$RUN" || exit 1
cd "$ROOT/tasks/slack-clone-course/student-files"
ln -sfn ../course-staff-files/final_task/environment/milestone-specs/milestone-2-specs spec
before=$(ls "$JOBS" 2>/dev/null)
LOG="$ROOT/runlogs/$RUN-$(date +%Y%m%d-%H%M%S).log"
COLUMNS=300 ./run_student_openai.sh 2 > "$LOG" 2>&1
job=$(comm -13 <(echo "$before") <(ls "$JOBS") | tail -1)
cost=$(python3 -c "import json,glob;print(round(json.load(open(glob.glob('$JOBS/$job/*/result.json')[0]))['agent_result']['cost_usd'],3))" 2>/dev/null)
echo "$RUN jobs/${job:-NOJOB} \$${cost:-?} $(basename "$LOG")" >> "$ROOT/runlogs/index.txt"
