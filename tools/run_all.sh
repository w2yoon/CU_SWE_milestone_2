#!/bin/bash
# Run the full plan: each run twice, in order
cd "$(dirname "$0")/.."
for r in 1.1 1.2 1.3 2.1 2.2 2.3 3 4 5.1 5.2 5.3; do for k in 1 2; do tools/run.sh $r; done; done
