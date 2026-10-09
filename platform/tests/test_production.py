"""Deployment checks use a fresh empty database and invented SMTP settings."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from flask import request,jsonify
from primoscore_server.database import Database
from primoscore_server.production import create_app


class ProductionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='primoscore-production-test-');self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/'empty.sqlite3');self.db.initialize()
        self.config=dict(PRIMOSCORE_DATABASE=str(self.db.path),PRIMOSCORE_AUTH_KEY=Fernet.generate_key().decode(),PRIMOSCORE_ORIGIN='https://primoscore.it',PRIMOSCORE_SMTP_HOST='smtp.example.invalid',PRIMOSCORE_SMTP_USER='info@primoscore.it',PRIMOSCORE_SMTP_PASSWORD='synthetic-not-used',PRIMOSCORE_MAIL_FROM='info@primoscore.it')

    def app(self):
        with patch.dict(os.environ,self.config,clear=True):return create_app()

    def test_secure_production_routes_no_demo_and_no_seed(self):
        app=self.app();client=app.test_client();base=dict(base_url='https://primoscore.it')
        for route in ('/','/accesso/consulente','/accesso/master','/accesso/cliente','/healthz'):
            with client.get(route,**base) as response:self.assertEqual(response.status_code,200)
        self.assertEqual(client.get('/__preview/',**base).status_code,404)
        self.assertFalse(any(r.rule.startswith('/api/demo') for r in app.url_map.iter_rules()))
        for route in ('/api/workspace','/api/workspace/import/template'):
            self.assertEqual(client.get(route,**base).status_code,401)
        response=client.get('/api/auth/csrf',**base)
        self.assertIn('__Host-ps_csrf=',response.headers['Set-Cookie'])
        for flag in ('Secure','HttpOnly','SameSite=Strict','Path=/'):self.assertIn(flag,response.headers['Set-Cookie'])
        self.assertIn('max-age=',response.headers['Strict-Transport-Security'])
        with self.db.transaction() as c:
            for table in ('accounts','cities','partners','clients','partner_imports'):self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_missing_settings_fail_closed(self):
        for key in self.config:
            with self.subTest(key=key),patch.dict(os.environ,{k:v for k,v in self.config.items() if k!=key},clear=True),self.assertRaises(RuntimeError):create_app()

    def test_proxy_uses_only_one_hop_and_rejects_foreign_host(self):
        app=self.app()
        @app.get('/test-proxy')
        def probe():return jsonify(ip=request.remote_addr,scheme=request.scheme,host=request.host)
        client=app.test_client()
        response=client.get('/test-proxy',base_url='http://primoscore.it',headers={'X-Forwarded-For':'198.51.100.1, 203.0.113.4','X-Forwarded-Proto':'https','X-Forwarded-Host':'attacker.invalid'})
        self.assertEqual(response.json,dict(ip='203.0.113.4',scheme='https',host='primoscore.it'))
        self.assertEqual(client.get('/healthz',base_url='https://attacker.invalid').status_code,400)
