#!/usr/bin/env bash
# Regenerate constraints.txt: one set of exact versions across all four services
# and the test tools, resolved for Python 3.12 on both Linux (Docker) and macOS.
#
# Needs uv (https://docs.astral.sh/uv/). Run from anywhere; run the tests after.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The editable shared package is ours, not a pinned dependency; its own
# dependencies are listed in its pyproject and added below.
for service in core-api ingestion-service prediction-service scoring-service; do
  grep -v '^-e' "$root/services/$service/requirements.txt" > "$work/$service.txt"
done
grep -v '^-' "$root/requirements-dev.txt" > "$work/dev.txt"
echo 'redis>=5.0' > "$work/f1-common.txt"

(cd "$work" && uv pip compile --quiet --universal --python-version 3.12 --no-header \
  core-api.txt ingestion-service.txt prediction-service.txt scoring-service.txt \
  dev.txt f1-common.txt -o body.txt)

{
  sed -n '1,/^$/p' "$root/constraints.txt"
  cat "$work/body.txt"
} > "$work/constraints.txt"
mv "$work/constraints.txt" "$root/constraints.txt"
echo "constraints.txt updated; now run the four services' tests."
