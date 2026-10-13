#!/usr/bin/env bash
# Verbindliche lokale Prüfungen aktivieren.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git config core.hooksPath .githooks
echo "Git-Hooks aktiviert"
