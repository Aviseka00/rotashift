#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="rotashift-team-test.zip"
rm -f "$OUT"
git archive --format=zip -o "$OUT" HEAD
echo "Created: $(pwd)/$OUT"
echo "Share this zip with your team. They should unzip, open README.md, and run: docker compose up --build"
