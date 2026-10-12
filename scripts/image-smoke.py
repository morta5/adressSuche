#!/usr/bin/env python3
"""Kontrollierte Migration und Seed vor dem unprivilegierten API-Start praktisch prüfen."""
import argparse
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    name = 'addresses-image-proof-' + secrets.token_hex(6)
    volume = name + '-data'
    try:
        subprocess.run(['docker', 'volume', 'create', volume], capture_output=True, check=True)
        base = ['docker', 'run', '--rm', '-v', volume + ':/data', args.image]
        # Der zweite Lauf muss denselben versionierten Stand ohne doppelte Daten akzeptieren.
        for _ in range(2):
            subprocess.run(base + ['python', 'scripts/migrate-staging.py'], capture_output=True, check=True)
        subprocess.run(['docker', 'run', '-d', '--name', name, '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '-v', volume + ':/data',
            '-e', 'ADDRESS_AUTO_MIGRATE=0', '-p', '127.0.0.1::8001', args.image], capture_output=True, check=True)
        address = subprocess.check_output(['docker', 'port', name, '8001/tcp'], text=True).strip()
        for attempt in range(60):
            try:
                with urllib.request.urlopen('http://' + address + '/openapi.json', timeout=2) as response:
                    assert response.status == 200
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if attempt == 59:
                    raise RuntimeError('Adress-API wird nach Migration nicht gesund') from None
                time.sleep(.5)
        with urllib.request.urlopen('http://' + address + '/autocomplete?query=Stagingallee&limit=5', timeout=5) as response:
            data = json.load(response)
            assert data and data[0]['name'] == 'Stagingallee', 'Synthetische Adresse nicht gefunden'
        with urllib.request.urlopen('http://' + address + '/photon/api/?q=Stagingallee&limit=5', timeout=5) as response:
            feature = json.load(response)['features'][0]
            assert feature['geometry']['coordinates'] == [10.0, 54.0]
        with urllib.request.urlopen('http://' + address + '/photon/reverse?lat=54&lon=10', timeout=5) as response:
            feature = json.load(response)['features'][0]
            assert feature['properties']['street'] == 'Stagingallee'
        assert subprocess.check_output(['docker', 'exec', name, 'id', '-u'], text=True).strip() != '0'
        print('Adress-Image: Migration wiederholbar, synthetische Suche und unprivilegierter Start bestätigt')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)


if __name__ == '__main__':
    main()
