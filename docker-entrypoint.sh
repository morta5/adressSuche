#!/bin/sh
set -e

DB_URL="${DATABASE_URL:-sqlite:////data/autocomplete.db}"
DB_DOWNLOAD_URL="${DB_DOWNLOAD_URL:-https://cloud.farshidhakimy.de/s/4A82YZZzXtMzkxs/download}"
DEFAULT_DB_PATH="/data/autocomplete.db"
DB_PATH="$DEFAULT_DB_PATH"
SHOULD_DOWNLOAD=1

case "$DB_URL" in
  sqlite:////*)
    DB_PATH="/${DB_URL#sqlite:////}"
    ;;
  sqlite:///*)
    DB_PATH="${DB_URL#sqlite:///}"
    ;;
  sqlite:/*)
    DB_PATH="$DEFAULT_DB_PATH"
    ;;
  *)
    SHOULD_DOWNLOAD=0
    ;;
esac

if [ "$SHOULD_DOWNLOAD" -eq 1 ]; then
  if [ ! -s "$DB_PATH" ]; then
    echo "Database missing at $DB_PATH. Downloading..."
    mkdir -p "$(dirname "$DB_PATH")"
    curl -fL "$DB_DOWNLOAD_URL" -o "$DB_PATH"
  else
    echo "Using existing database at $DB_PATH"
  fi
else
  echo "Non-sqlite DATABASE_URL detected; skipping DB download"
fi

if [ "$#" -eq 0 ]; then
  set -- uvicorn main:app --host 0.0.0.0 --port 8001
fi

exec "$@"
