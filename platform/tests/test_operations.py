import test_customer
BASE=test_customer.BASE
from test_auth import AuthFixture,profile
from primoscore_server.operations import Operations
from primoscore_server.notifications import send_operational
from primoscore_server.web import create_app
from primoscore_server.auth import AuthError
from primoscore_core.intake import complete_result
from unittest.mock import Mock,patch

class OperationsTest(test_customer.CustomerTest):
    def test_privacy_change_invalidates_stale_profile_without_losing_controller(self):
        stale=profile();stale.pop('password');stale['revision']=0
        self.customer.configure(self.session,{
            'action':'privacy','privacy_url':'https://studio.example.invalid/privacy',
            'privacy_version':'2026-09-30','controller_name':'Nuovo titolare',
            'controller_email':'nuova-privacy@example.invalid','revision':0,
        })
        with self.assertRaises(AuthError) as rejected:
            Operations(self.auth).profile(self.session,stale)
        self.assertEqual(rejected.exception.status,409)
        with self.db.transaction() as c:
            current=c.execute('SELECT controller_name,controller_email,profile_revision FROM consultant_profiles WHERE tenant_id=?',(self.advisor['tenant_id'],)).fetchone()
        self.assertEqual(tuple(current),('Nuovo titolare','nuova-privacy@example.invalid',1))

    def test_profile_revision_email_and_suspend(self):
        op=Operations(self.auth)
        d=profile();d.pop('password');d['revision']=0;d['mobile']='3331111111'
        op.profile(self.session,d)
        with self.assertRaises(AuthError):op.profile(self.session,d)
        op.email(self.session,{'email':'changed@example.invalid'})
        self.assertEqual(self.account('advisor@example.invalid')['id'],self.advisor['id'])
        self.auth.verify_email(self.token(self.advisor['id']))
        self.assertEqual(self.account('changed@example.invalid')['id'],self.advisor['id'])
        with self.assertRaises(AuthError):self.auth.identity(self.session)
        op.status(self.master['session_token'],{'tenant_id':self.advisor['tenant_id'],'status':'suspended','reason':'Test'})
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT status FROM tenants WHERE id=?',(self.advisor['tenant_id'],)).fetchone()[0],'suspended')
        op.status(self.master['session_token'],{'tenant_id':self.advisor['tenant_id'],'status':'active','reason':'Test'})

    def test_notification_export_and_erasure(self):
        guest=self.intake();self.save(guest)
        self.customer.complete({'revision':1},guest=guest)
        op=Operations(self.auth);archive=op.export(self.session)
        self.assertEqual(len(archive['data']['clients']),1)
        self.assertNotIn('auth_credentials',archive['data'])
        mailer=Mock();self.assertEqual(send_operational(self.auth,mailer)['sent'],2)
        self.assertEqual({call.args[0]['to'] for call in mailer.send.call_args_list},{'advisor@example.invalid','client@example.invalid'})
        self.assertNotIn('monthlyIncome',str(mailer.send.call_args))
        client=self.customer.get(guest=guest)['client']
        op.erase(self.session,{'client_id':client['id'],'revision':client['revision'],'confirmation':'ANONIMIZZA'})
        with self.assertRaises(AuthError):self.customer.get(guest=guest)
        archive=op.export(self.session)
        self.assertEqual(archive['data']['clients'][0]['email'],'')
        self.assertEqual(archive['data']['assessments'][0]['answers'],{})
        with self.db.transaction() as c:self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_queued_notice_cannot_move_to_another_account_with_the_old_email(self):
        guest=self.intake();self.save(guest);self.customer.complete({'revision':1},guest=guest)
        op=Operations(self.auth);op.email(self.session,{'email':'changed@example.invalid'})
        self.auth.verify_email(self.token(self.advisor['id']))
        with self.db.transaction() as c:
            c.execute("INSERT INTO accounts(id,tenant_id,role,email,status,created_at) VALUES('replacement',?,'consultant','advisor@example.invalid','active',?)",(self.advisor['tenant_id'],self.time))
            c.execute("INSERT INTO auth_credentials(account_id,email_verified,password_changed_at) VALUES('replacement',1,?)",(self.time,))
        mailer=Mock();result=send_operational(self.auth,mailer)
        self.assertEqual(result,{'sent':1,'failed':0,'cancelled':1})
        self.assertEqual(mailer.send.call_args.args[0]['purpose'],'report')

    def test_test_qr_resume_does_not_authenticate_and_restart_is_scoped(self):
        from primoscore_server.customer import TEST_CONTACT,TEST_STUDIO_SLUG
        from uuid import uuid4
        guest=self.intake()
        with self.assertRaises(AuthError):self.customer.restart_test({'revision':0},guest=guest)
        with self.db.transaction() as c:c.execute('UPDATE tenants SET slug=? WHERE id=?',(TEST_STUDIO_SLUG,self.advisor['tenant_id']))
        first=self.customer.acquire({**self.payload(),**TEST_CONTACT},ip='test-first')
        second=self.customer.acquire({**self.payload(),**TEST_CONTACT,'request_id':str(uuid4())},ip='test-second')
        self.assertTrue(second['resume_required']);self.assertNotIn('guest_token',second)
        self.save(first['guest_token']);self.customer.complete({'revision':1},guest=first['guest_token'])
        self.customer.restart_test({'revision':1},guest=first['guest_token'])
        state=self.customer.get(guest=first['guest_token']);self.assertIsNone(state['result']);self.assertEqual(state['questionnaire']['answers'],{})
        with self.assertRaises(AuthError):self.customer.restart_test({'revision':2})

    def test_operations_are_scoped_and_master_only(self):
        op=Operations(self.auth)
        guest=self.intake();client=self.customer.get(guest=guest)['client']
        with self.assertRaises(AuthError):op.health(self.session)
        with self.assertRaises(AuthError):op.retry_mail(self.session)
        with self.assertRaises(AuthError):op.status(self.session,{'tenant_id':self.advisor['tenant_id'],'status':'suspended','reason':'Test'})
        with self.assertRaises(AuthError):op.export(self.session,tenant='another-studio')
        with self.assertRaises(AuthError):op.erase(self.session,{'client_id':client['id'],'revision':client['revision'],'confirmation':'NO'})
        self.assertEqual(self.customer.get(guest=guest)['client']['email'],'client@example.invalid')

class BoundaryTest(AuthFixture):
    def test_registration_csrf_two_tabs_and_health(self):
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth);browser=app.test_client();base=self.auth.origin
        first=browser.get('/api/auth/csrf',base_url=base).json['csrf']
        second=browser.get('/api/auth/csrf',base_url=base).json['csrf'];self.assertEqual(first,second)
        response=browser.post('/api/auth/register',base_url=base,json=profile(),headers={'Origin':base,'X-CSRF-Token':first})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(browser.get('/healthz',base_url=base).status_code,200)
        self.db.path.unlink()
        self.assertEqual(browser.get('/healthz',base_url=base).status_code,503)

    def test_inconsistent_istat_input_is_rejected(self):
        with self.assertRaises(ValueError):complete_result({**BASE,'istatRegion':7})

def load_tests(loader,tests,pattern):
    import unittest
    return unittest.TestSuite(cls(name) for cls in (OperationsTest,BoundaryTest) for name in cls.__dict__ if name.startswith('test_'))
