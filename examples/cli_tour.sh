#!/usr/bin/env bash
# A tour of the benchtrace CLI. Offline: bundled benchmarks and mock models, no API keys.
#   bash examples/cli_tour.sh
# Uses a throwaway BENCHTRACE_HOME so it does not touch your own runs.
set -euo pipefail

export BENCHTRACE_HOME="${BENCHTRACE_HOME:-$(mktemp -d)}" COLUMNS="${COLUMNS:-120}"
bt() { echo; echo "\$ benchtrace $*"; uv run --quiet benchtrace "$@"; }
# Run a command, show its output, and keep it in $out for picking out ids.
capture() { out=$(bt "$@"); echo "$out"; }

# 1. What can I run?
bt catalog toy-tools

# 2. Estimate cost before running, then approve it with a budget cap.
#    btmock prices are made up; real models need prices in ~/.benchtrace/pricing.yaml.
capture quote create toy-tools -m btmock/strong --sample-size 3
quote=$(grep -o 'quote_[0-9a-f]*' <<< "$out" | tail -1)
bt quote approve "$quote" --cap 0.01

# 3. Run two models on the same benchmark. The last line prints the compare command.
capture run toy-tools -m btmock/strong -m btmock/weak --limit 15 --yes
read -r strong weak < <(grep -o 'compare run_[0-9a-f]* run_[0-9a-f]*' <<< "$out" | cut -d' ' -f2-)

# 4. Paired comparison: per-sample changes, confidence interval, McNemar's test.
bt compare "$strong" "$weak"

# 5. Drill into one changed sample: its span tree, then where the two runs diverged.
sample=$(uv run --quiet benchtrace compare "$strong" "$weak" --json | uv run --quiet python -c \
  "import json,sys; print(next(r['sample_id'] for r in json.load(sys.stdin)['rows'] if r['change'] == 'regression'))")
bt show "$weak" --outcome incorrect --samples 5
bt trace "$weak" "$sample"
bt diff "$strong" "$weak" "$sample"

# 6. Turn failures into a dataset of drafts for review.
capture dataset create "calculator failures"
dataset=$(grep -o 'ds_[0-9a-f]*' <<< "$out")
bt dataset add "$dataset" --run "$weak" --outcome incorrect --split dev
bt dataset show "$dataset"

echo
echo "Done. Data is in $BENCHTRACE_HOME. Browse it with: BENCHTRACE_HOME=$BENCHTRACE_HOME uv run benchtrace serve"
