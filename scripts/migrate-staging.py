#!/usr/bin/env python3
"""Festgelegtes Staging-Schema und synthetische Adressen vor dem Rollout vorbereiten."""
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phonetic import german_phonetic_phrase, cologne_phonetic_phrase
from utils import normalize_string, normalize_compact, consonant_key

url = os.environ.get('DATABASE_URL', 'sqlite:////data/autocomplete.db')
if not url.startswith('sqlite:///'):
    raise SystemExit('Die Staging-Migration benötigt eine SQLite-Datei')
path = Path(url.removeprefix('sqlite:///'))
path.parent.mkdir(parents=True, exist_ok=True)
connection = sqlite3.connect(path)
try:
    connection.execute('CREATE TABLE IF NOT EXISTS staging_schema_version (version INTEGER PRIMARY KEY)')
    if connection.execute('SELECT 1 FROM staging_schema_version WHERE version=1').fetchone() is None:
        extension = ROOT / 'spellfix.so'
        if not extension.exists():
            raise RuntimeError('Die erforderliche Spellfix-Erweiterung fehlt')
        connection.enable_load_extension(True)
        connection.load_extension(str(extension))
        connection.enable_load_extension(False)
        connection.executescript('BEGIN;\n' + (ROOT / 'migrations/001-staging-schema.sql').read_text())
        connection.execute('INSERT INTO staging_schema_version VALUES (1)')
    name, city = 'Stagingallee', 'Teststadt'
    row = connection.execute('SELECT id FROM streets WHERE name=? AND city=?', (name, city)).fetchone()
    if row is None:
        connection.execute("""INSERT INTO streets
            (name, normalized_name, normalized_search, city, postal_code, latitude, longitude,
             phonetic_german, phonetic_cologne, consonant_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, normalize_string(name), normalize_compact(name), city, '00000', 54.0, 10.0,
             german_phonetic_phrase(name), cologne_phonetic_phrase(name), consonant_key(name)))
        street_id = connection.execute('SELECT last_insert_rowid()').fetchone()[0]
        connection.execute('INSERT INTO addresses (street_id,house_number,latitude,longitude) VALUES (?,?,?,?)',
                           (street_id, '1', 54.0, 10.0))
        connection.execute('INSERT INTO street_trigram (rowid,normalized_name) VALUES (?,?)',
                           (street_id, normalize_string(name)))
        connection.execute('INSERT INTO street_spellfix (rowid,word) VALUES (?,?)',
                           (street_id, normalize_string(name)))
    connection.commit()
    print('Staging-Schema 1 und synthetische Adresse vorbereitet')
finally:
    connection.close()
