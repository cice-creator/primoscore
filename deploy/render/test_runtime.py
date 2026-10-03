import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location('render_runtime', Path(__file__).with_name('runtime.py'))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTest(unittest.TestCase):
    def test_key_persists_and_existing_database_is_not_reset(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PRIMOSCORE_STATE_DIR': directory}, clear=True):
            runtime.configure()
            key = os.environ['PRIMOSCORE_AUTH_KEY']
            from primoscore_server.database import Database
            db = Database(os.environ['PRIMOSCORE_DATABASE']); db.initialize()
            runtime.configure()
            self.assertEqual(key, os.environ['PRIMOSCORE_AUTH_KEY'])
            self.assertEqual(Path(directory, 'auth.key').stat().st_mode & 0o777, 0o600)
            runtime.backup(Path(directory))
            self.assertEqual(len(list(Path(directory, 'backups').glob('*.sqlite3'))), 1)
            Path(directory, 'auth.key').unlink()
            with self.assertRaises(RuntimeError): runtime.configure()

    def test_brevo_request_is_transactional_and_failure_is_retryable(self):
        from primoscore_server.brevo_mail import BrevoMailer
        with patch.dict(os.environ, {'BREVO_API_KEY': 'synthetic-test', 'PRIMOSCORE_MAIL_FROM': 'info@primoscore.it'}):
            mailer = BrevoMailer()
        response = MagicMock(); response.__enter__.return_value.status = 201
        with patch('primoscore_server.brevo_mail.urlopen', return_value=response) as send:
            mailer.send({'purpose': 'verify', 'to': 'test@example.invalid', 'url': 'https://primoscore.it/accesso/conferma#token=synthetic'})
            req = send.call_args.args[0]
            self.assertEqual(req.full_url, 'https://api.brevo.com/v3/smtp/email')
            data = json.loads(req.data)
            self.assertEqual(data['sender']['email'], 'info@primoscore.it')
            self.assertEqual(data['to'], [{'email': 'test@example.invalid'}])
            self.assertNotIn('listIds', data)
        response.__enter__.return_value.status = 500
        with patch('primoscore_server.brevo_mail.urlopen', return_value=response), self.assertRaises(OSError):
            mailer.send({'purpose': 'verify', 'to': 'test@example.invalid', 'url': 'https://primoscore.it/'})


if __name__ == '__main__': unittest.main()

class RestoreTest(unittest.TestCase):
    def test_backup_restores_database_and_encrypted_material(self):
        from cryptography.fernet import Fernet
        import sqlite3
        from primoscore_server.database import Database
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PRIMOSCORE_STATE_DIR': directory}, clear=True):
            root=Path(directory);runtime.configure();db=Database(os.environ['PRIMOSCORE_DATABASE']);db.initialize()
            cipher=Fernet((root/'auth.key').read_bytes())
            with db.transaction() as c:
                c.execute('CREATE TABLE restore_probe (value TEXT)')
                c.execute('INSERT INTO restore_probe VALUES (?)',(cipher.encrypt(b'synthetic recovery check').decode(),))
            runtime.backup(root)
            backup=next((root/'backups').glob('*.sqlite3'))
            with sqlite3.connect(backup) as restored:
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(restored.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(cipher.decrypt(restored.execute('SELECT value FROM restore_probe').fetchone()[0].encode()),b'synthetic recovery check')
                expected_migrations=len(list((Path(__file__).resolve().parents[2]/'platform/primoscore_server/migrations').glob('*.sql')))
                self.assertEqual(restored.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],expected_migrations)

    def test_periodic_backup_failure_does_not_stop_runtime(self):
        with patch.object(runtime, 'backup', side_effect=OSError('volume unavailable')):
            self.assertFalse(runtime.periodic_backup(Path('/data')))
