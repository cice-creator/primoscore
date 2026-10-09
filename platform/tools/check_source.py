"""Read-only integrity and upstream drift checks; never imports the source app."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(content):
    return hashlib.sha256(content).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--ref', help='Explicit Git revision, required with --repository')
    args = parser.parse_args()
    if bool(args.repository) != bool(args.ref):
        parser.error('--repository and --ref must be supplied together')
    manifest = json.loads((ROOT / 'source-manifest.json').read_text())
    failures = []
    for item in manifest['extracted']:
        path = ROOT / item['destination']
        if not path.is_file() or digest(path.read_bytes()) != item['sha256']:
            failures.append('Pacchetto modificato: ' + item['destination'])
    if args.repository:
        try:
            commit = subprocess.check_output(
                ['git', '-C', str(args.repository), 'rev-parse', '--verify', '--end-of-options', args.ref + '^{commit}'],
                stderr=subprocess.DEVNULL, text=True).strip()
        except subprocess.CalledProcessError:
            parser.error('Revisione Git non disponibile')
        for item in manifest['tracked_sources']:
            try:
                data = subprocess.check_output(
                    ['git', '-C', str(args.repository), 'show', commit + ':' + item['source']],
                    stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                failures.append('File sorgente assente: ' + item['source'])
                continue
            if digest(data) != item['sha256']:
                failures.append('Revisione diversa: ' + item['source'])
    for failure in failures:
        print(failure)
    if failures:
        print('Controllo non superato; nessun aggiornamento applicato.')
        return 1
    print('Integrità verificata' + ('; sorgenti allineati alla revisione indicata.' if args.repository else '.'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
