"""Explicit administration; no default users or automatic email sending."""

import argparse
import getpass
import json
import os
from pathlib import Path

from .database import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init','check','init-key','serve','bootstrap-master','send-mail'])
    parser.add_argument('--database', required=True, help='Explicit path outside dist/')
    parser.add_argument('--key-file')
    parser.add_argument('--origin',default='http://127.0.0.1:4173')
    parser.add_argument('--local',action='store_true')
    args = parser.parse_args()
    database = Database(args.database)
    if args.action=='init-key':
        from cryptography.fernet import Fernet
        if not args.key_file:
            parser.error('--key-file richiesto')
        path = Path(args.key_file).resolve()
        if 'dist' in path.parts:
            parser.error('Chiave fuori dalla cartella pubblicata richiesta')
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as file:
            file.write(Fernet.generate_key())
        print('Chiave locale creata. Il valore non viene mostrato.')
        return 0
    if args.action in ('serve','bootstrap-master','send-mail'):
        from .auth import Auth
        if args.key_file:
            if not args.local:
                parser.error('--key-file è riservato a --local; usa PRIMOSCORE_AUTH_KEY in produzione')
            path = Path(args.key_file)
            if path.stat().st_mode & 0o077:
                parser.error('La chiave deve essere leggibile soltanto dal proprietario')
            key = path.read_bytes().strip()
        else:
            key = os.environ['PRIMOSCORE_AUTH_KEY']
        auth = Auth(database,key,args.origin)
        if args.action=='serve':
            if not args.local:
                parser.error('Il comando serve richiede --local; per la produzione usa un server WSGI')
            from .web import create_app
            from urllib.parse import urlsplit
            origin = urlsplit(args.origin)
            app = create_app(database,key,args.origin,local=True,auth=auth)
            app.run(host='127.0.0.1',port=origin.port or 4173,debug=False,use_reloader=False)
        elif args.action=='bootstrap-master':
            default_email = os.environ.get('PRIMOSCORE_MASTER_EMAIL','info@primoscore.it')
            email = input(f'Email del Master [{default_email}]: ').strip() or default_email
            password = getpass.getpass('Password (almeno 12 caratteri): ')
            if password != getpass.getpass('Ripeti la password: '):
                parser.error('Le password non coincidono')
            auth.bootstrap_master(email,password)
            print('Master predisposto. Email di verifica in coda; Authenticator sarà obbligatorio.')
        else:
            if args.local:
                parser.error('L’invio non è consentito in modalità locale')
            from .mail import SMTPMailer,send_pending
            print(json.dumps(send_pending(auth,SMTPMailer())))
        return 0
    if args.action == 'init':
        database.initialize()
    with database.transaction() as connection:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('schema_migrations','sqlite_sequence')")]
        counts = {table: connection.execute('SELECT COUNT(*) FROM "' + table.replace('"','""') + '"').fetchone()[0] for table in tables}
        versions = [r[0] for r in connection.execute('SELECT version FROM schema_migrations ORDER BY version')]
    print(json.dumps({'database': str(database.path), 'integrity': integrity,
                      'foreign_key_errors': len(foreign_keys), 'migrations': versions,
                      'counts': counts, 'empty': not any(counts.values())}, indent=2))
    return 0 if integrity == 'ok' and not foreign_keys else 1


if __name__ == '__main__':
    raise SystemExit(main())
