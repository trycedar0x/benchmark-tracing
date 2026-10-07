#!/usr/bin/env bash
# Records demo media for the README and launch pages: web UI screenshots and a walkthrough video
# (webm, mp4, gif), and terminal GIFs of the CLI. Offline: bundled benchmarks and mock models.
#   npm --prefix web ci && npm --prefix web run build && npx --prefix web playwright install chromium
#   brew install vhs ffmpeg
#   bash scripts/demo/record.sh [out-dir]
# Uses a throwaway BENCHTRACE_HOME and its own port, so it does not touch your runs or server.
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT=$(mkdir -p "${1:-docs/media}" && cd "${1:-docs/media}" && pwd)
PORT=${PORT:-8322}
export BENCHTRACE_HOME=$(mktemp -d) BENCHTRACE_INGEST_CONTENT=full BENCHTRACE_URL="http://127.0.0.1:$PORT"
bt() { COLUMNS=120 uv run --quiet benchtrace "$@"; }

uv run --quiet benchtrace serve --port "$PORT" >"$BENCHTRACE_HOME/serve.log" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null; wait $server 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -sf "$BENCHTRACE_URL/api/health" >/dev/null && break; sleep 1; done

echo "Seeding demo data in $BENCHTRACE_HOME"
read -r A B < <(bt run toy-tools -m btmock/strong -m btmock/weak --limit 30 --yes |
  grep -o 'compare run_[0-9a-f]* run_[0-9a-f]*' | cut -d' ' -f2-)
bt run toy-arith -m btmock/strong -m btmock/flaky --limit 30 --yes >/dev/null
uv run --quiet python examples/sdk_trace_agent.py >/dev/null
bash examples/otlp_json.sh >/dev/null
DS=$(bt dataset create "calculator failures" | grep -o 'ds_[0-9a-f]*')
bt dataset add "$DS" --run "$B" --outcome incorrect --split dev >/dev/null
SAMPLE=$(bt compare "$A" "$B" --json | uv run --quiet python -c \
  "import json,sys; print(next(r['sample_id'] for r in json.load(sys.stdin)['rows'] if r['change'] == 'regression'))")

echo "Recording the web UI"
node scripts/demo/record-ui.mjs "$OUT" "$A" "$B" "$DS"
ffmpeg -loglevel error -y -i "$OUT/ui-walkthrough.webm" -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart \
  "$OUT/ui-walkthrough.mp4"
ffmpeg -loglevel error -y -i "$OUT/ui-walkthrough.webm" -vf \
  "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" \
  "$OUT/ui-walkthrough.gif"

echo "Recording the CLI"
tapes=$(mktemp -d)
header() {
  cat <<TAPE
Output "$OUT/$1"
Set Shell bash
Set FontSize 15
Set Width 1270
Set Height 760
Set Padding 24
Set Theme "GitHub Dark"
Set TypingSpeed 35ms
Set WaitTimeout 120s
Env BENCHTRACE_HOME "$BENCHTRACE_HOME"
Hide
Type 'benchtrace() { uv run --quiet benchtrace "\$@"; }; cd "$PWD"; clear'
Enter
Show
TAPE
}
{
  header cli-run.gif
  cat <<'TAPE'
Type "benchtrace run toy-tools -m btmock/strong -m btmock/weak --limit 30"
Sleep 400ms
Enter
Wait+Screen /Compare:/
Sleep 4s
TAPE
} >"$tapes/run.tape"
{
  header cli-compare-trace-diff.gif
  cat <<TAPE
Type "benchtrace compare $A $B"
Sleep 400ms
Enter
Wait+Screen /Inspect a change/
Sleep 4s
Type "clear; benchtrace trace $B $SAMPLE"
Sleep 400ms
Enter
Wait+Screen /scorer/
Sleep 3s
Type "clear; benchtrace diff $A $B $SAMPLE"
Sleep 400ms
Enter
Wait+Screen /scorer/
Sleep 5s
TAPE
} >"$tapes/compare.tape"
{
  header cli-sdk-trace.gif
  cat <<TAPE
Env BENCHTRACE_URL "$BENCHTRACE_URL"
Type "uv run python examples/sdk_trace_agent.py"
Sleep 400ms
Enter
Wait+Screen /closed \(3.3 spans\)/
Sleep 4s
TAPE
} >"$tapes/sdk.tape"
for tape in "$tapes"/*.tape; do vhs -q "$tape"; done
rm -rf "$tapes"

echo "Done:"
ls -lh "$OUT"
