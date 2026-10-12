#!/usr/bin/env bash
# Hausnummern- und kontrollierte Staging-Startprüfungen; kein externer Datendienst.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mode="${1:---full}"
[[ "$mode" == --fast || "$mode" == --full ]] || exit 2
py="${PYTHON:-python3}"
"$py" -c 'import pytest, fastapi, sqlalchemy, shapely, aiosqlite' || exit 3
export PYTHONDONTWRITEBYTECODE=1
"$py" -m pytest test_utils_house_number.py test_staging_schema.py test_photon_compat.py -q -p no:cacheprovider
