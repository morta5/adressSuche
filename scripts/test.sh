#!/usr/bin/env bash
# Kurzlebige Parser-Fixtures, keine Abfrage der laufenden Adressdatenbank.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:---full}" in --fast|--full) ;; *) exit 2 ;; esac
"${PYTHON:-python3}" -m pytest test_utils_house_number.py -q -p no:cacheprovider
