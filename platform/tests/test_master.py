from test_customer import CustomerTest
from primoscore_server.master import Master
from primoscore_server.auth import AuthError
from primoscore_server.operations import Operations
from primoscore_server.web import create_app

class MasterTest(CustomerTest):
    def test_summary_and_qr_attribution_do_not_multiply_assessments(self):
        guest=self.intake();self.save(guest);self.customer.complete({'revision':1},guest=guest)
        master=Master(self.auth);token=self.master['session_token'];tenant=self.advisor['tenant_id']
        metrics=master.studios(token)['studios'][0]
        self.assertEqual((metrics['qr'],metrics['leads'],metrics['completed']),(1,1,1))
        detail=master.detail(token,tenant,'Verifica')
        self.assertEqual(detail['lots'][0]['leads'],1)
        leads=master.detail(token,tenant,'Verifica',detail['lots'][0]['id'])['leads']
        self.assertEqual(len(leads),1);self.assertEqual(leads[0]['email'],'client@example.invalid')
        self.assertNotIn('answers_json',str(detail));self.assertNotIn('totp_encrypted',str(detail))
        Operations(self.auth).status(token,{'tenant_id':tenant,'status':'suspended','reason':'Verifica'})
        self.assertEqual(master.detail(token,tenant,'Verifica')['metrics']['leads'],1)

    def test_authorization_scope_and_audit(self):
        master=Master(self.auth);token=self.master['session_token'];tenant=self.advisor['tenant_id']
        with self.assertRaises(AuthError):master.studios(self.session)
        with self.assertRaises(AuthError):master.detail(self.session,tenant,'Verifica')
        with self.assertRaises(AuthError):master.detail(token,tenant,'')
        with self.assertRaises(AuthError):master.detail(token,'missing','Verifica')
        with self.assertRaises(AuthError):master.detail(token,tenant,'Verifica','missing')
        master.detail(token,tenant,'Verifica autorizzata')
        with self.db.transaction() as c:
            row=c.execute("SELECT reason FROM audit_events WHERE tenant_id=? AND action='master_studio_read'",(tenant,)).fetchone()
        self.assertEqual(row[0],'Verifica autorizzata')

    def test_anonymized_contacts_excluded(self):
        guest=self.intake();client=self.customer.get(guest=guest)['client']
        Operations(self.auth).erase(self.session,{'client_id':client['id'],'revision':client['revision'],'confirmation':'ANONIMIZZA'})
        master=Master(self.auth);detail=master.detail(self.master['session_token'],self.advisor['tenant_id'],'Verifica')
        self.assertEqual(detail['metrics']['leads'],0);self.assertEqual(detail['lots'][0]['leads'],0)

    def test_master_http_boundaries_and_profile_redirect(self):
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth);browser=app.test_client();base=self.auth.origin
        self.assertEqual(browser.get('/master/',base_url=base).status_code,302)
        self.assertEqual(browser.get('/api/master/studios',base_url=base).status_code,401)
        from urllib.parse import urlsplit
        browser.set_cookie('__Host-ps_session',self.master['session_token'],domain=urlsplit(base).hostname)
        self.assertEqual(browser.get('/master/',base_url=base).status_code,200)
        self.assertEqual(browser.get('/accesso/profilo',base_url=base).location,'/master/')
        self.assertEqual(browser.get('/api/master/studios',base_url=base).status_code,200)
        url='/api/master/studios/'+self.advisor['tenant_id']
        self.assertEqual(browser.get(url,base_url=base).status_code,400)
        self.assertEqual(browser.get(url,base_url=base,headers={'X-Master-Reason':'Verifica'}).status_code,200)
