#!/usr/bin/env bash
# Record the three demo sessions as asciinema casts + plain-text transcripts.
# Sessions share one workspace copy and one memory file, so session 2 genuinely
# starts a NEW process that reads preferences saved by session 1.
#
#   bash scripts/record_demos.sh             # deterministic replay LLM (default)
#   LLM=anthropic bash scripts/record_demos.sh   # real model (needs ANTHROPIC_API_KEY)
set -euo pipefail
cd "$(dirname "$0")/.."
LLM="${LLM:-replay}"
OUT=demos/recordings
WS=demos/.workspace
export AGENT_MEMORY_PATH="$PWD/demos/.memory.json"
export COLUMNS=110 LINES=50
rm -rf "$WS" "$AGENT_MEMORY_PATH" && mkdir -p "$OUT"
cp -r target_project "$WS"
find "$WS" -name __pycache__ -prune -exec rm -rf {} +

for i in 1 2 3; do
  if [ "$LLM" = replay ]; then
    LLM_ARGS="--replay-file demos/fixtures/session$i.yaml"
  else
    LLM_ARGS="--llm $LLM"
  fi
  CMD="python3 -m coding_agent.cli --workspace $WS $LLM_ARGS --script demos/session$i.txt --typing-delay 0.02"
  if command -v asciinema >/dev/null; then
    asciinema rec --overwrite --cols 110 --rows 50 -q -c "$CMD" "$OUT/session$i.cast"
    # plain transcript (ANSI stripped) for reading on GitHub
    python3 - "$OUT/session$i.cast" "$OUT/session$i.txt" <<'PY'
import json, re, sys
src, dst = sys.argv[1:3]
text = "".join(json.loads(l)[2] for l in open(src).read().splitlines()[1:] if l.strip())
text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r", "", text)
open(dst, "w").write(text)
PY
  else
    $CMD | tee "$OUT/session$i.txt"
  fi
done
echo "recordings in $OUT (play with: asciinema play $OUT/session1.cast)"
