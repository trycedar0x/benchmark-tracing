#!/usr/bin/env bash
# A tour of the everybench CLI. Offline: bundled benchmarks and mock models, no API keys.
#   bash examples/cli_tour.sh
# Uses a throwaway EVERYBENCH_HOME so it does not touch your own runs.
set -euo pipefail

export EVERYBENCH_HOME="${EVERYBENCH_HOME:-$(mktemp -d)}" COLUMNS="${COLUMNS:-120}"
ee() { echo; echo "\$ everybench $*"; uv run --quiet everybench "$@"; }
# Run a command, show its output, and keep it in $out for picking out ids.
capture() { out=$(ee "$@"); echo "$out"; }

# 1. What can I run?
ee catalog toy-tools

# 2. Estimate cost before running, then approve it with a budget cap.
#    mock prices are made up; real models need prices in ~/.everybench/pricing.yaml.
capture quote create toy-tools -m mock/strong --sample-size 3
quote=$(grep -o 'quote_[0-9a-f]*' <<< "$out" | tail -1)
ee quote approve "$quote" --cap 0.01

# 3. Run two models on the same benchmark. The last line prints the compare command.
capture run toy-tools -m mock/strong -m mock/weak --limit 15 --yes
read -r strong weak < <(grep -o 'compare run_[0-9a-f]* run_[0-9a-f]*' <<< "$out" | cut -d' ' -f2-)

# 4. Paired comparison: per-sample changes, confidence interval, McNemar's test.
ee compare "$strong" "$weak"

# 5. Drill into one changed sample: its span tree, then where the two runs diverged.
sample=$(uv run --quiet everybench compare "$strong" "$weak" --json | uv run --quiet python -c \
  "import json,sys; print(next(r['sample_id'] for r in json.load(sys.stdin)['rows'] if r['change'] == 'regression'))")
ee show "$weak" --outcome incorrect --samples 5
ee trace "$weak" "$sample"
ee diff "$strong" "$weak" "$sample"

# 6. Turn failures into a dataset of drafts for review.
capture dataset create "calculator failures"
dataset=$(grep -o 'ds_[0-9a-f]*' <<< "$out")
ee dataset add "$dataset" --run "$weak" --outcome incorrect --split dev
ee dataset show "$dataset"

echo
echo "Done. Data is in $EVERYBENCH_HOME. Browse it with: EVERYBENCH_HOME=$EVERYBENCH_HOME uv run everybench serve"
