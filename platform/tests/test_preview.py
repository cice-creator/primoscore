"""Guided preview is disposable and never adds shortcuts to the normal app."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from preview_site import create_preview
from primoscore_server.web import create_app


class PreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=create_preview(4174);cls.app.testing=True;cls.base='http://127.0.0.1:4174'
    @classmethod
    def tearDownClass(cls):cls.app.extensions['preview_tempdir'].cleanup()
    def setUp(self):self.client=self.app.test_client()
    def switch(self,view):
        csrf=self.client.get('/api/auth/csrf',base_url=self.base).json['csrf']
        return self.client.post('/api/demo/switch',base_url=self.base,json={'view':view},headers={'Origin':self.base,'X-CSRF-Token':csrf})

    def test_all_tour_destinations_and_roles(self):
        self.assertEqual(self.client.get('/__preview/',base_url=self.base).status_code,200)
        for view in self.app.extensions['preview_roles']:
            with self.subTest(view=view):
                response=self.switch(view);self.assertEqual(response.status_code,200)
                page=self.client.get(response.json['path'],base_url=self.base)
                self.assertEqual(page.status_code,200);self.assertIn(b'demo-toolbar',page.data)
                if view=='consultant':
                    data=self.client.get('/api/workspace',base_url=self.base).json
                    self.assertGreater(len(data['partners']),0)
                    self.assertGreater(len(self.client.get('/api/workspace/customers',base_url=self.base).json['clients']),0)
                if view in ('result','partial','draft'):
                    data=self.client.get('/api/customer',base_url=self.base).json
                    if view=='result':self.assertEqual(data['result']['totalScore'],49)
                    elif view=='partial':self.assertTrue(data['result']['partial'])
                    else:self.assertIsNone(data['result'])
                if view=='master':self.assertEqual(len(self.client.get('/api/master/consultants',base_url=self.base).json['consultants']),2)

    def test_demo_switch_requires_csrf_and_uses_separate_cookies(self):
        self.client.set_cookie('ps_session','untouched-real-session')
        response=self.client.post('/api/demo/switch',base_url=self.base,json={'view':'master'})
        self.assertEqual(response.status_code,403)
        response=self.switch('consultant')
        self.assertIn('ps_demo_session=',';'.join(response.headers.getlist('Set-Cookie')))
        self.assertEqual(self.client.get_cookie('ps_session').value,'untouched-real-session')
        self.assertEqual(self.switch('invented').status_code,400)

    def test_normal_application_never_registers_demo_switch(self):
        auth=self.app.extensions['primoscore_auth'];normal=create_app(auth.db,b'unused',self.base,local=True,auth=auth)
        client=normal.test_client();csrf=client.get('/api/auth/csrf',base_url=self.base).json['csrf']
        response=client.post('/api/demo/switch',base_url=self.base,json={'view':'master'},headers={'Origin':self.base,'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code,404)
        self.assertEqual(client.get('/__preview/',base_url=self.base).status_code,404)
        with self.assertRaises(ValueError):create_app(auth.db,b'unused','https://primoscore.example',auth=auth,cookie_prefix='ps_demo_')
        self.assertNotEqual(auth.db.path,Path('var/primoscore.sqlite3').resolve())
