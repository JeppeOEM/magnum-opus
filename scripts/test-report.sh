#!/usr/bin/env bash
# Usage: test-report.sh <label> <output-file> <cmd...>
# Runs <cmd>, streams output live to stdout, then writes a markdown report.
set -uo pipefail

LABEL=$1
OUT=$2
shift 2

mkdir -p "$(dirname "$OUT")"

START=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

TMPOUT=$(mktemp)
"$@" 2>&1 | tee "$TMPOUT"
STATUS=${PIPESTATUS[0]}

OUTPUT=$(cat "$TMPOUT")
rm -f "$TMPOUT"

if [ "$STATUS" -eq 0 ]; then
  RESULT="✅ PASSED"
else
  RESULT="❌ FAILED"
fi

cat > "$OUT" <<MDEOF
# $LABEL Test Results

| | |
|---|---|
| **Result** | $RESULT |
| **Date** | $START |
| **Branch** | \`$BRANCH\` |
| **Commit** | \`$COMMIT\` |

## Output

\`\`\`
$OUTPUT
\`\`\`
MDEOF

echo "==> Report saved to $OUT"
exit $STATUS
