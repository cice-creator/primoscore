"""Single-instance Render entry point; no legacy data or account imports."""
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'platform'))


def configure():
    os.umask(0o077)
    directory = Path(os.environ.get('PRIMOSCORE_STATE_DIR', '/app/data/primoscore-v2'))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.geteuid() == 0:
        os.chown(directory, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    from cryptography.fernet import Fernet
    keyfile = directory / 'auth.key'
    with (directory / '.key.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not keyfile.exists():
            if (directory / 'primoscore.sqlite3').exists():
                raise RuntimeError('Chiave mancante per archivio esistente: ripristinare il backup.')
            with keyfile.open('x') as target:
                target.write(Fernet.generate_key().decode())
                target.flush()
                os.fsync(target.fileno())
        key = keyfile.read_text().strip()
        Fernet(key)
    os.environ['PRIMOSCORE_AUTH_KEY'] = key
    os.environ['PRIMOSCORE_DATABASE'] = str(directory / 'primoscore.sqlite3')
    os.environ.setdefault('PRIMOSCORE_ORIGIN', 'https://primoscore.it')
    os.environ.setdefault('PRIMOSCORE_MAIL_FROM', 'info@primoscore.it')
    os.environ.setdefault('PRIMOSCORE_MAIL_TRANSPORT', 'brevo')
    os.environ['PYTHONPATH'] = str(ROOT / 'platform') + os.pathsep + os.environ.get('PYTHONPATH', '')
    return directory


def backup(directory):
    target = directory / 'backups'
    target.mkdir(mode=0o700, exist_ok=True)
    destination = target / ('primoscore-' + str(time.time_ns()) + '.sqlite3')
    with sqlite3.connect(os.environ['PRIMOSCORE_DATABASE']) as source, sqlite3.connect(destination) as out:
        source.backup(out)
        if out.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup non valido.')
    # Only rotate the backups created by this version, never legacy files.
    for old in sorted(target.glob('primoscore-*.sqlite3'))[:-7]:
        old.unlink()


def mail_loop(db):
    from primoscore_server.auth import Auth
    from primoscore_server.mail import SMTPMailer, send_pending
    from primoscore_server.brevo_mail import BrevoMailer
    auth = Auth(db, os.environ['PRIMOSCORE_AUTH_KEY'], os.environ['PRIMOSCORE_ORIGIN'])
    mailer = BrevoMailer() if os.environ['PRIMOSCORE_MAIL_TRANSPORT'] == 'brevo' else SMTPMailer()
    # The supervisor guarantees exactly one delivery process on this disk.
    with db.transaction() as c:
        c.execute("UPDATE auth_mail SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END WHERE status='sending'")
    while True:
        result = send_pending(auth, mailer)
        if any(result.values()):
            print('mail_delivery ' + json.dumps(result), flush=True)
        time.sleep(30)


def supervise():
    stopping = False
    children = []
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        port = str(int(os.environ.get('PORT', '4173')))
        children.append(subprocess.Popen([sys.executable, '-m', 'gunicorn', '--bind', '0.0.0.0:' + port,
            '--workers', '1', '--threads', '2', '--timeout', '60', '--graceful-timeout', '20',
            '--worker-tmp-dir', '/tmp', '--no-control-socket', '--error-logfile', '-', 'primoscore_server.production:create_app()'], cwd=ROOT / 'platform'))
        children.append(subprocess.Popen([sys.executable, __file__, 'mail']))
        while not stopping:
            if any(p.poll() is not None for p in children):
                raise RuntimeError('Un processo del servizio si è arrestato.')
            time.sleep(1)
    finally:
        for p in children:
            if p.poll() is None:
                p.terminate()
        deadline = time.monotonic() + 25
        for p in children:
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def main():
    directory = configure()
    from primoscore_server.database import Database
    db = Database(os.environ['PRIMOSCORE_DATABASE'])
    role = sys.argv[1] if len(sys.argv) > 1 else 'web'
    if role == 'web':
        from primoscore_server.web import from_environment
        from_environment()  # Validate before starting either child.
        if db.path.exists():
            backup(directory)
        db.initialize()
        supervise()
    elif role == 'mail':
        mail_loop(db)
    elif role == 'backup':
        backup(directory)
        print('Backup consistente completato.')
    elif role == 'bootstrap-master':
        import getpass
        from primoscore_server.auth import Auth
        password = getpass.getpass('Password Master (almeno 12 caratteri): ')
        if password != getpass.getpass('Ripeti password: '):
            raise SystemExit('Le password non coincidono.')
        Auth(db, os.environ['PRIMOSCORE_AUTH_KEY'], os.environ['PRIMOSCORE_ORIGIN']).bootstrap_master('info@primoscore.it', password)
        print('Master predisposto. Confermare email e configurare Authenticator.')
    elif role == 'status':
        from urllib.request import Request, urlopen
        req = Request('https://api.brevo.com/v3/senders', headers={'api-key': os.environ['BREVO_API_KEY'], 'Accept': 'application/json', 'User-Agent': 'Primoscore/1.0'})
        try:
            with urlopen(req, timeout=15) as response:
                senders = json.load(response).get('senders', [])
            print('Mittente info@primoscore.it attivo:', any(x.get('email', '').lower() == 'info@primoscore.it' and x.get('active') for x in senders))
        except OSError:
            print('Verifica Brevo non riuscita; controllare la configurazione del provider.')
        with db.transaction() as c:
            for table in ('accounts', 'clients', 'partners', 'cities'):
                print(table, c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])
    else:
        raise SystemExit('Comando non valido.')


if __name__ == '__main__':
    main()
