#!/usr/bin/env bash
# Ausschließlich benötigte Quellen schreibgeschützt verwenden.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mode="${1:---full}"
[[ "$mode" == --fast || "$mode" == --full ]] || exit 2
docker run --rm --tmpfs /work:rw,exec,size=1g -v "$(pwd):/source:ro" \
  -e TEST_MODE="$mode" python:3.13-slim bash -ec '
    cd /work
    cp /source/*.py /source/requirements.txt .
    mkdir scripts
    cp /source/scripts/test.sh scripts/
    python -m pip install --no-cache-dir -r requirements.txt pytest pytest-asyncio
    scripts/test.sh "$TEST_MODE"
  '
