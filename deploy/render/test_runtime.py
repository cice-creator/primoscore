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
