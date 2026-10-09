"""Only temporary synthetic studios; never use the local operational database."""
import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import unittest
from uuid import uuid4
from pypdf import PdfReader
from test_auth import AuthFixture
from primoscore_server.auth import AuthError
from primoscore_server.repository import Repository
from primoscore_server.workspace import Workspace
from primoscore_server.voucher_pdf import card
from primoscore_server.web import create_app

TODAY=date.today().isoformat()

class WorkspaceTest(AuthFixture):
    def setUp(self):
        super().setUp()
        self.actor,self.login,_,self.master=self.active_consultant()
        self.session=self.login['session_token'];self.ws=Workspace(self.auth)

    def cmd(self,action,**data):return self.ws.command(self.session,dict(action=action,data=data,request_id=str(uuid4())))
    def snapshot(self):return self.ws.snapshot(self.session)
    def city(self,name='Città sintetica'):return self.cmd('city.create',name=name)
    def partner(self,city=None,name='Partner sintetico',details=None):
        city=city or self.city()
        return self.cmd('partner.create',name=name,city_id=city['id'],category='Agenzie immobiliari',details=details or {})
    def partner_row(self,id):return next(p for p in self.snapshot()['partners'] if p['id']==id)
    def print(self,lot,quantity):return self.cmd('print.add',lot_id=lot,quantity=quantity,printed_on=TODAY,notes='Prova')
    def deliver(self,partner,quantity):return self.cmd('delivery.create',partner_id=partner,quantity=quantity,delivered_on=TODAY)
    def api(self):
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth);app.testing=True
        client=app.test_client();client.set_cookie('__Host-ps_session',self.session,domain='primoscore.example')
        csrf=client.get('/api/auth/csrf',base_url=self.auth.origin).json['csrf']
        return client,{'Origin':self.auth.origin,'X-CSRF-Token':csrf}

    def test_starts_empty_and_city_creates_unique_zero_stock_lot(self):
        self.assertEqual(self.snapshot()['partners'],[]);self.assertEqual(self.snapshot()['cities'],[])
        c=self.city();lot=self.snapshot()['voucher_lots'][0]
        self.assertEqual((lot['city_id'],lot['printed'],lot['delivered'],lot['available']),(c['id'],0,0,0))
        with self.assertRaises(AuthError):self.city('CITTÀ SINTETICA')
        self.assertEqual(len(self.snapshot()['voucher_lots']),1)

    def test_campaign_is_atomic_and_name_unique(self):
        c=self.cmd('campaign.create',name='Campagna sintetica',kind='website')
        s=self.snapshot();self.assertEqual(len(s['cities']),0)
        self.assertEqual((s['partners'][0]['id'],s['voucher_lots'][0]['id']),(c['partner_id'],c['lot_id']))
        with self.assertRaises(AuthError):self.cmd('campaign.create',name='campagna sintetica',kind='flyer')
        self.assertEqual(len(self.snapshot()['partners']),1)
        self.print(c['lot_id'],10);self.deliver(c['partner_id'],3)
        self.assertEqual(self.snapshot()['voucher_lots'][0]['available'],7)

    def test_stock_edits_delete_and_insufficient_rollback(self):
        c=self.city();p=self.partner(c);self.print(c['lot_id'],10);d=self.deliver(p['id'],8)
        with self.assertRaises(AuthError):self.deliver(p['id'],3)
        with self.assertRaises(AuthError):self.cmd('delivery.update',id=d['id'],revision=0,quantity=11,delivered_on=TODAY)
        self.assertEqual(self.snapshot()['voucher_lots'][0]['available'],2)
        self.cmd('delivery.update',id=d['id'],revision=0,quantity=5,delivered_on=TODAY)
        with self.assertRaises(AuthError):self.cmd('delivery.delete',id=d['id'],revision=0)
        self.cmd('delivery.delete',id=d['id'],revision=1)
        self.assertEqual(self.snapshot()['voucher_lots'][0]['available'],10)

    def test_retry_is_idempotent_and_different_payload_rejected(self):
        c=self.city();payload=dict(action='print.add',data=dict(lot_id=c['lot_id'],quantity=10,printed_on=TODAY),request_id=str(uuid4()))
        first=self.ws.command(self.session,payload)
        self.assertEqual(self.ws.command(self.session,payload),first)
        payload['data']['quantity']=20
        with self.assertRaises(AuthError):self.ws.command(self.session,payload)
        s=self.snapshot();self.assertEqual(len(s['print_runs']),1);self.assertEqual(s['voucher_lots'][0]['printed'],10)

    def test_concurrent_deliveries_cannot_overdraw(self):
        c=self.city();p=self.partner(c);self.print(c['lot_id'],10)
        def attempt(_):
            try:self.deliver(p['id'],7);return True
            except AuthError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sorted(results),[False,True]);self.assertEqual(self.snapshot()['voucher_lots'][0]['available'],3)

    def test_city_move_preserves_delivery_and_activity_origin(self):
        a=self.city('Città A sintetica');b=self.city('Città B sintetica');p=self.partner(a)
        self.print(a['lot_id'],20);self.print(b['lot_id'],20);self.deliver(p['id'],4)
        self.cmd('activity.create',partner_id=p['id'],revision=0,kind='Telefonata',occurred_on=TODAY,outcome='Prova',nextAction='Richiamare',nextDate=TODAY)
        row=self.partner_row(p['id']);self.cmd('partner.update',id=p['id'],revision=row['revision'],name=row['name'],city_id=b['id'],category=row['category'],details=row['details'])
        self.deliver(p['id'],6);s=self.snapshot()
        self.assertEqual([d['lot_id'] for d in s['deliveries']],[a['lot_id'],b['lot_id']])
        self.assertEqual(s['activities'][0]['details']['city_id'],a['id'])

    def test_archiving_restores_previous_suspended_status(self):
        c=self.cmd('campaign.create',name='Campagna',kind='company');p=c['partner_id'];self.print(c['lot_id'],10)
        self.cmd('partner.status',id=p,revision=0,status='inactive')
        self.cmd('partner.status',id=p,revision=1,status='archived')
        self.cmd('partner.status',id=p,revision=2,status='restore')
        self.assertEqual(self.partner_row(p)['status'],'inactive')
        with self.assertRaises(AuthError):self.deliver(p,1)
        code=self.snapshot()['voucher_lots'][0]['code']
        with self.assertRaises(AuthError):self.ws.public_voucher(code)
        self.cmd('partner.status',id=p,revision=3,status='active')
        self.assertEqual(self.ws.public_voucher(code)['email'],self.actor['email'])
        self.assertNotIn('tenant_id',self.ws.public_voucher(code))

    def test_stale_partner_change_and_bad_values_are_rejected(self):
        c=self.city();p=self.partner(c);self.cmd('partner.status',id=p['id'],revision=0,status='inactive')
        with self.assertRaises(AuthError):self.cmd('partner.status',id=p['id'],revision=0,status='active')
        for det in ({'leads':1,'qualifiedLeads':2},{'nextDate':TODAY},{'priority':'Massima'},{'firstContact':'2999-01-01'},{'leads':True}):
            with self.subTest(det=det),self.assertRaises(AuthError):self.partner(c,details=det)
        for q in (True,-1,0,1.2,'2'):
            with self.subTest(q=q),self.assertRaises(AuthError):self.print(c['lot_id'],q)

    def test_foreign_studio_ids_and_exports_denied(self):
        other,login,_=self.enrolled('second@example.invalid');self.auth.activate(self.master['session_token'],other['tenant_id'])
        ws2=Workspace(self.auth);foreign=ws2.command(login['session_token'],dict(action='city.create',data={'name':'Segreto altro studio'},request_id=str(uuid4())))
        for method,args in ((self.ws.snapshot,()),(self.ws.export,('json',)),(self.ws.voucher,(foreign['lot_id'],))):
            with self.assertRaises(AuthError):method(self.session,*args,tenant=other['tenant_id'])
        with self.assertRaises(AuthError):self.print(foreign['lot_id'],10)
        with self.assertRaises(AuthError):self.partner(foreign)
        self.assertNotIn('Segreto',self.ws.export(self.session,'json'))

    def test_customer_and_suspended_account_denied(self):
        repo=Repository(self.db,self.actor['id']);client=repo.create('clients',{'first_name':'Cliente','email':'customer@example.invalid'})
        self.auth.invite_customer(self.session,client['id']);account=self.account('customer@example.invalid')
        from test_auth import PASSWORD
        self.auth.reset_password(self.token(account['id']),PASSWORD,invite=True)
        with self.db.transaction() as c:slug=c.execute('SELECT slug FROM tenants WHERE id=?',(self.actor['tenant_id'],)).fetchone()[0]
        customer=self.auth.login(account['email'],PASSWORD,'customer',slug,'customer')
        with self.assertRaises(AuthError):self.ws.snapshot(customer['session_token'])
        with self.db.transaction() as c:c.execute("UPDATE tenants SET status='suspended' WHERE id=?",(self.actor['tenant_id'],))
        with self.assertRaises(AuthError):self.snapshot()

    def test_master_requires_reason_and_audits_reads_and_writes(self):
        token=self.master['session_token'];t=self.actor['tenant_id']
        with self.assertRaises(AuthError):self.ws.snapshot(token,tenant=t)
        self.ws.snapshot(token,tenant=t,reason='Assistenza sintetica')
        self.ws.command(token,dict(action='city.create',data={'name':'Città master'},request_id=str(uuid4())),tenant=t,reason='Assistenza sintetica')
        with self.db.transaction() as c:
            rows=c.execute("SELECT action,reason FROM audit_events WHERE reason='Assistenza sintetica'").fetchall()
        self.assertEqual({r['action'] for r in rows},{'read_workspace','city.create'})

    def test_export_filters_formula_escaping_and_no_auth_material(self):
        c=self.city();self.partner(c,name='=HYPERLINK("https://example.invalid")',details={'nextAction':'+cmd','notes':'@formula'})
        self.partner(c,name='Non selezionato',details={'priority':'Alta'})
        result=self.ws.export(self.session,'csv',filters={'priority':'Media'})
        self.assertIn("'=HYPERLINK",result);self.assertIn("'+cmd",result);self.assertIn("'@formula",result);self.assertNotIn('Non selezionato',result)
        backup=json.loads(self.ws.export(self.session,'json'))
        self.assertNotIn('auth_credentials',backup);self.assertNotIn('accounts',backup)

    def test_activity_and_print_histories_are_immutable(self):
        c=self.city();p=self.partner(c);self.print(c['lot_id'],1)
        self.cmd('activity.create',partner_id=p['id'],revision=0,kind='Visita',occurred_on=TODAY)
        for table in ('activities','print_runs'):
            with self.assertRaises(sqlite3.IntegrityError),self.db.transaction() as conn:conn.execute('DELETE FROM '+table)

    def test_http_routes_csrf_auth_exports_and_public_qr(self):
        client,headers=self.api();base=self.auth.origin
        self.assertEqual(client.get('/consulente/',base_url=base).status_code,200)
        payload=dict(action='city.create',data={'name':'Città HTTP'},request_id=str(uuid4()))
        self.assertEqual(client.post('/api/workspace/commands',json=payload,base_url=base).status_code,403)
        response=client.post('/api/workspace/commands',json=payload,headers=headers,base_url=base);self.assertEqual(response.status_code,200)
        lot=response.json['lot_id'];snapshot=client.get('/api/workspace',base_url=base).json
        code=snapshot['voucher_lots'][0]['code']
        for path in ('/api/workspace/export.csv','/api/workspace/export.json',f'/api/workspace/lots/{lot}/qr.svg',f'/api/workspace/lots/{lot}/voucher.pdf','/v/'+code):
            with self.subTest(path=path):self.assertEqual(client.get(path,base_url=base).status_code,200)
        anon=client.application.test_client()
        for path in ('/api/workspace','/api/workspace/export.csv',f'/api/workspace/lots/{lot}/qr.svg'):
            self.assertEqual(anon.get(path,base_url=base).status_code,401)
        self.assertEqual(anon.get('/consulente/',base_url=base).status_code,302)

    def test_bad_http_shapes_do_not_bypass_or_crash(self):
        client,headers=self.api()
        for payload in ([],{},dict(action='partner.update',data={'id':{},'revision':0},request_id=str(uuid4())),dict(action='city.create',data={'name':'X','tenant_id':'forged'},request_id=str(uuid4()))):
            response=client.post('/api/workspace/commands',json=payload,headers=headers,base_url=self.auth.origin)
            self.assertEqual(response.status_code,400)

class PDFTest(unittest.TestCase):
    def test_two_sided_trim_bleed_and_local_warning(self):
        pdf=PdfReader(io.BytesIO(card('Città sintetica','http://127.0.0.1:4173/v/PS-SYNTHETIC',local=True)))
        self.assertEqual(len(pdf.pages),2)
        mm=72/25.4
        for page in pdf.pages:
            self.assertAlmostEqual(float(page.mediabox.width),61*mm,places=3)
            self.assertAlmostEqual(float(page.trimbox.width),55*mm,places=3)
            self.assertAlmostEqual(float(page.trimbox.left),3*mm,places=3)
        self.assertIn('NON DISTRIBUIRE',pdf.pages[0].extract_text())
        self.assertIn('SOLO PER LA PROVA',pdf.pages[1].extract_text())
        real=PdfReader(io.BytesIO(card('Campagna','https://primoscore.example/v/PS-SYNTHETIC')))
        self.assertNotIn('LOCALE',''.join(p.extract_text() for p in real.pages))
