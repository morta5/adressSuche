# Staging-Prüfungen der Adresssuche

`scripts/test.sh --fast` und `--full` prüfen acht Hausnummernfälle und drei
Schema-Startfälle mit Python 3.13. Installation: `python -m pip install
-r requirements.txt pytest pytest-asyncio`. Ohne lokale Werkzeuge:
`scripts/test-docker.sh --fast` beziehungsweise `--full`. Die Testkopie
enthält ausschließlich Python-Quellen, Anforderungen und den Testeinstieg.
`scripts/install-hooks.sh` aktiviert die verbindlichen Git-Prüfungen.

`Dockerfile.staging` verwendet eine unprivilegierte Wolfi-Laufzeit ohne
Paketinstaller. Der ursprüngliche GitHub-Scan beanstandete Betriebssystem
und alte FastAPI-/Starlette-/AnyIO-Abhängigkeiten. Der aktualisierte lokale
HIGH-/CRITICAL-Scan ist ohne Befund.

`python3 scripts/image-smoke.py --image <Image>` führt die versionierte
SQLite-Migration zweimal aus und startet danach mit `ADDRESS_AUTO_MIGRATE=0`.
Der reale HTTP-Start und die Suche nach der synthetischen `Stagingallee`
sind bestanden. Die Schemaabschaltung war zunächst in zwei Tests rot;
alle elf Fälle sind jetzt grün. Die SQL-Datei ist ein festgelegter Stand,
kein Schema-Autogenerieren beim Rollout. Fehlgeschlagene Migrationen müssen
den Deployment-Schritt sperren. Außerhalb des Staging-Schalters bleibt der
bisherige Start erhalten.

Offen: umfassende regionale Daten, Ranking-/Sprachfälle gegen vorbereitete
Datensätze und der Gesamtstack-Nachweis im Cluster. Vorhandene manuelle
Diagnoseskripte gegen lokale beziehungsweise echte Daten sind damit nicht
als automatisiert bestanden erklärt.
