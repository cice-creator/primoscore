import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
import pyotp

from primoscore_server import Database,Repository
from primoscore_server.auth import Auth,AuthError,digest
from primoscore_server.mail import send_pending
from primoscore_server.web import create_app

PASSWORD='Synthetic password for tests only!'


def profile(email='advisor@example.invalid'):
    return dict(controller_name='Studio sintetico',controller_email='privacy@example.invalid',first_name='Nome',last_name='Sintetico',email=email,password=PASSWORD,mobile='+39 3330000000',landline='0800000000',office_address='Indirizzo di prova',office_postcode='00000',office_city='Comune sintetico',office_province='XX',oam_number='M12345',ivass_registered='yes',ivass_number='A000000000')


class AuthFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='primoscore-auth-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/'test.sqlite3')
        self.db.initialize()
        self.time=1900000000
        self.key=Fernet.generate_key()
        # Small cost only in this explicitly injected test hasher. Production uses
        # the library's default Argon2id parameters, with no environment override.
        self.auth=Auth(self.db,self.key,'https://primoscore.example',clock=lambda:self.time,hasher=PasswordHasher(time_cost=1,memory_cost=8192,parallelism=1))

    def account(self,email):
        with self.db.transaction() as c:
            return dict(c.execute('SELECT * FROM accounts WHERE email=?',(email,)).fetchone())

    def mail(self,account):
        with self.db.transaction() as c:
            row=c.execute('SELECT payload_encrypted FROM auth_mail WHERE account_id=? ORDER BY rowid DESC',(account,)).fetchone()
        return json.loads(self.auth.cipher.decrypt(row[0].encode()))

    def token(self,account):
        return parse_qs(urlsplit(self.mail(account)['url']).fragment)['token'][0]

    def enrolled(self,email='advisor@example.invalid',master=False):
        if master:self.auth.bootstrap_master(email,PASSWORD)
        else:self.auth.register(profile(email),email)
        actor=self.account(email)
        self.auth.verify_email(self.token(actor['id']))
        step=self.auth.login(email,PASSWORD,actor['role'],'',email)
        setup=self.auth.enrollment(step['step_token'])
        complete=self.auth.factor(step['step_token'],pyotp.TOTP(setup['secret']).at(self.time),email)
        return actor,complete,setup['secret']

    def active_consultant(self,email='advisor@example.invalid'):
        actor,complete,secret=self.enrolled(email)
        _,master,_=self.enrolled('master@example.invalid',True)
        self.auth.activate(master['session_token'],actor['tenant_id'])
        return actor,complete,secret,master

class AuthTest(AuthFixture):
    def test_registration_complete_private_and_no_operational_seed(self):
        self.auth.register(profile(),'ip')
        actor=self.account('advisor@example.invalid')
        self.assertEqual(actor['status'],'pending')
        with self.db.transaction() as c:
            for table in ('cities','partners','clients','voucher_lots','campaigns'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            stored=c.execute('SELECT * FROM auth_credentials').fetchone()
            self.assertTrue(stored['password_hash'].startswith('$argon2id$'))
            self.assertNotIn(PASSWORD,stored['password_hash'])
            self.assertIsNone(stored['totp_encrypted'])
            p=c.execute('SELECT * FROM consultant_profiles').fetchone()
            self.assertEqual((p['oam_number'],p['ivass_number'],p['landline']),('M12345','A000000000','0800000000'))
            cipher=c.execute('SELECT payload_encrypted FROM auth_mail').fetchone()[0]
            self.assertNotIn('advisor@example.invalid',cipher)
        with self.assertRaises(AuthError):self.auth.login(actor['email'],PASSWORD,'consultant','','ip')

    def test_registration_rejects_missing_profile_and_role_escalation(self):
        for changes in ({'oam_number':'X123'},{'landline':''},{'ivass_number':''},{'role':'master'}):
            with self.subTest(changes=changes),self.assertRaises(AuthError):
                self.auth.register({**profile(),**changes},str(changes))
        self.auth.register({**profile('other@example.invalid'),'ivass_registered':'no','ivass_number':''},'valid')

    def test_duplicate_registration_does_not_overwrite_account(self):
        self.auth.register(profile(),'one')
        self.auth.register({**profile(),'password':'Another synthetic password'},'two')
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],1)
            self.assertTrue(self.auth.hasher.verify(c.execute('SELECT password_hash FROM auth_credentials').fetchone()[0],PASSWORD))

    def test_email_token_is_single_use_expiring_and_not_a_login(self):
        self.auth.register(profile(),'ip')
        actor=self.account('advisor@example.invalid');token=self.token(actor['id'])
        with self.db.transaction() as c:self.assertNotEqual(c.execute('SELECT token_hash FROM auth_tokens').fetchone()[0],token)
        with self.assertRaises(AuthError):self.auth.identity(token)
        self.auth.verify_email(token)
        with self.assertRaises(AuthError):self.auth.verify_email(token)
        self.auth.register(profile('expired@example.invalid'),'other')
        expired=self.token(self.account('expired@example.invalid')['id']);self.time+=86401
        with self.assertRaises(AuthError):self.auth.verify_email(expired)

    def test_enrollment_requires_confirmation_and_master_activation(self):
        actor,result,secret=self.enrolled()
        self.assertEqual(len(result['recovery_codes']),8)
        self.assertFalse(self.auth.identity(result['session_token'])['active'])
        with self.assertRaises(AuthError):self.auth.identity(result['session_token'],full=True)
        with self.db.transaction() as c:
            stored=c.execute('SELECT totp_encrypted FROM auth_credentials WHERE account_id=?',(actor['id'],)).fetchone()[0]
            self.assertNotEqual(stored,secret)
            hashes=[r[0] for r in c.execute('SELECT code_hash FROM auth_recovery_codes')]
            self.assertTrue(all(code not in hashes for code in result['recovery_codes']))
        _,master,_=self.enrolled('master@example.invalid',True)
        self.auth.activate(master['session_token'],actor['tenant_id'])
        self.assertTrue(self.auth.identity(result['session_token'],full=True)['active'])

    def test_master_cannot_activate_unverified_registration(self):
        self.auth.register(profile(),'ip')
        actor=self.account('advisor@example.invalid')
        _,master,_=self.enrolled('master@example.invalid',True)
        with self.assertRaises(AuthError):self.auth.activate(master['session_token'],actor['tenant_id'])

    def test_totp_replay_rejected_and_correct_next_code_accepted(self):
        actor,_,secret=self.enrolled()
        step=self.auth.login(actor['email'],PASSWORD,'consultant','','ip')
        with self.assertRaises(AuthError):self.auth.factor(step['step_token'],pyotp.TOTP(secret).at(self.time),'ip')
        self.time+=31
        result=self.auth.factor(step['step_token'],pyotp.TOTP(secret).at(self.time),'ip')
        self.assertIn('session_token',result)
        with self.assertRaises(AuthError):self.auth.factor(step['step_token'],pyotp.TOTP(secret).at(self.time),'ip')

    def test_failed_factor_attempts_persist_and_exhaust_challenge(self):
        actor,_,secret=self.enrolled()
        step=self.auth.login(actor['email'],PASSWORD,'consultant','','ip')
        for _ in range(6):
            with self.assertRaises(AuthError):self.auth.factor(step['step_token'],'invalid','ip')
        self.time+=31
        with self.assertRaises(AuthError):self.auth.factor(step['step_token'],pyotp.TOTP(secret).at(self.time),'ip')
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT attempts FROM auth_steps').fetchone()[0],6)

    def test_recovery_code_replaces_authenticator_revokes_sessions_and_old_codes(self):
        actor,prior,old_secret=self.enrolled()
        step=self.auth.login(actor['email'],PASSWORD,'consultant','','ip')
        result=self.auth.factor(step['step_token'],prior['recovery_codes'][0],'ip')
        self.assertEqual(result['step'],'enroll')
        with self.assertRaises(AuthError):self.auth.identity(prior['session_token'])
        setup=self.auth.enrollment(result['step_token'])
        self.assertNotEqual(setup['secret'],old_secret)
        complete=self.auth.factor(result['step_token'],pyotp.TOTP(setup['secret']).at(self.time),'ip')
        self.assertEqual(len(complete['recovery_codes']),8)
        step=self.auth.login(actor['email'],PASSWORD,'consultant','','ip')
        with self.assertRaises(AuthError):self.auth.factor(step['step_token'],prior['recovery_codes'][1],'ip')

    def test_reset_revokes_sessions_and_steps_but_keeps_mfa(self):
        actor,result,secret=self.enrolled()
        challenge=self.auth.login(actor['email'],PASSWORD,'consultant','','ip')
        self.auth.request_email(actor['email'],'consultant','','reset','ip')
        token=self.token(actor['id']);new_password='New synthetic long password'
        self.auth.reset_password(token,new_password)
        with self.assertRaises(AuthError):self.auth.identity(result['session_token'])
        with self.assertRaises(AuthError):self.auth.factor(challenge['step_token'],'000000','ip')
        with self.assertRaises(AuthError):self.auth.reset_password(token,PASSWORD)
        self.assertEqual(self.auth.login(actor['email'],new_password,'consultant','','ip')['step'],'otp')

    def test_unknown_recovery_response_does_not_reveal_account(self):
        self.assertIsNone(self.auth.request_email('missing@example.invalid','consultant','','reset','ip'))
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_mail').fetchone()[0],0)

    def test_customer_invitation_is_bound_to_client_and_studio(self):
        actor,result,_,master=self.active_consultant()
        client=Repository(self.db,actor['id']).create('clients',{'first_name':'Synthetic client','email':'customer@example.invalid'})
        self.auth.invite_customer(result['session_token'],client['id'])
        customer=self.account('customer@example.invalid')
        self.auth.reset_password(self.token(customer['id']),PASSWORD,invite=True)
        slug=self.auth.identity(result['session_token'])['slug']
        self.assertIn('studio='+slug,self.mail(customer['id'])['url'])
        with self.assertRaises(AuthError):self.auth.login(customer['email'],PASSWORD,'customer','different-studio','ip')
        logged=self.auth.login(customer['email'],PASSWORD,'customer',slug,'ip')
        identity=self.auth.identity(logged['session_token'],full=True)
        self.assertEqual(identity['tenant_id'],actor['tenant_id'])
        with self.assertRaises(AuthError):self.auth.master_consultants(logged['session_token'])
        with self.assertRaises(AuthError):self.auth.invite_customer(logged['session_token'],client['id'])

    def test_sessions_expire_logout_and_suspension_are_effective(self):
        actor,result,_=self.enrolled()
        token=result['session_token'];self.time+=1801
        with self.assertRaises(AuthError):self.auth.identity(token)
        self.time-=1801
        self.auth.logout(token)
        with self.assertRaises(AuthError):self.auth.identity(token)
        with self.db.transaction() as c:c.execute("UPDATE accounts SET status='suspended' WHERE id=?",(actor['id'],))
        with self.assertRaises(AuthError):self.auth.login(actor['email'],PASSWORD,'consultant','','ip')

    def test_reinviting_pending_customer_replaces_old_link(self):
        actor,result,_,_=self.active_consultant()
        client=Repository(self.db,actor['id']).create('clients',{'first_name':'Synthetic client','email':'invite@example.invalid'})
        self.auth.invite_customer(result['session_token'],client['id'])
        customer=self.account('invite@example.invalid');old=self.token(customer['id'])
        self.auth.invite_customer(result['session_token'],client['id'])
        new=self.token(customer['id'])
        self.assertNotEqual(old,new)
        with self.assertRaises(AuthError):self.auth.reset_password(old,PASSWORD,invite=True)
        self.auth.reset_password(new,PASSWORD,invite=True)

    def test_master_bootstrap_is_one_time_and_has_no_public_password(self):
        self.auth.bootstrap_master('master@example.invalid',PASSWORD)
        with self.assertRaises(AuthError):self.auth.bootstrap_master('second@example.invalid',PASSWORD)
        with self.assertRaises(AuthError):self.auth.login('master@example.invalid',PASSWORD,'consultant','','ip')

    def test_rate_limit_survives_failure_transactions(self):
        for _ in range(10):
            with self.assertRaises(AuthError):self.auth.login('unknown@example.invalid',PASSWORD,'consultant','','ip')
        with self.assertRaises(AuthError) as caught:self.auth.login('unknown@example.invalid',PASSWORD,'consultant','','ip')
        self.assertEqual(caught.exception.status,429)
        self.time+=901
        with self.assertRaises(AuthError) as caught:self.auth.login('unknown@example.invalid',PASSWORD,'consultant','','ip')
        self.assertEqual(caught.exception.status,401)

    def test_mail_worker_uses_only_current_tokens_and_bounded_retries(self):
        self.auth.register(profile(),'ip');actor=self.account('advisor@example.invalid')
        self.auth.request_email(actor['email'],'consultant','','verify','ip')
        class Mailer:
            def __init__(self):self.messages=[]
            def send(self,payload):self.messages.append(payload)
        sender=Mailer();result=send_pending(self.auth,sender)
        self.assertEqual(result,{'sent':1,'failed':0,'cancelled':1})
        self.assertEqual(len(sender.messages),1)
        self.assertEqual(send_pending(self.auth,sender)['sent'],0)
        self.auth.request_email(actor['email'],'consultant','','verify','ip')
        class Broken:
            def send(self,payload):raise OSError('Synthetic unavailable server')
        for _ in range(3):self.assertEqual(send_pending(self.auth,Broken())['failed'],1)
        self.assertEqual(send_pending(self.auth,Broken())['failed'],0)


class WebAuthTest(AuthFixture):
    def setUp(self):
        super().setUp()
        self.app=create_app(self.db,self.key,'https://primoscore.example',auth=self.auth)
        self.app.testing=True
        self.browser=self.app.test_client()
        self.csrf=self.browser.get('/api/auth/csrf',base_url=self.auth.origin).json['csrf']

    def post(self,path,data,**kwargs):
        response=self.browser.post(path,json=data,base_url=self.auth.origin,headers={'Origin':self.auth.origin,'X-CSRF-Token':self.csrf},**kwargs)
        if response.is_json and response.json.get('csrf'):self.csrf=response.json['csrf']
        return response

    def test_http_password_does_not_grant_access_before_second_factor(self):
        self.auth.register(profile(),'ip');actor=self.account('advisor@example.invalid');self.auth.verify_email(self.token(actor['id']))
        response=self.post('/api/auth/login',{'email':actor['email'],'password':PASSWORD,'role':'consultant'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('step_token',response.json)
        self.assertEqual(self.browser.get('/api/auth/session',base_url=self.auth.origin).status_code,401)
        setup=self.browser.get('/api/auth/enrollment',base_url=self.auth.origin)
        self.assertEqual(setup.status_code,200)
        result=self.post('/api/auth/factor',{'code':pyotp.TOTP(setup.json['secret']).at(self.time)})
        self.assertEqual(result.status_code,200)
        self.assertNotIn('session_token',result.json)
        cookies=';'.join(result.headers.getlist('Set-Cookie'))
        self.assertIn('__Host-ps_session',cookies)
        for expected in ('Secure','HttpOnly','SameSite=Strict','Path=/'):self.assertIn(expected,cookies)
        self.assertNotIn('Domain=',cookies)
        self.assertEqual(self.browser.get('/api/auth/session',base_url=self.auth.origin).status_code,200)
        self.assertEqual(self.browser.get('/api/master/consultants',base_url=self.auth.origin).status_code,403)
        self.assertEqual(self.post('/api/auth/logout',{}).status_code,200)
        self.assertEqual(self.browser.get('/api/auth/session',base_url=self.auth.origin).status_code,401)

    def test_http_csrf_origin_host_and_content_type(self):
        for headers in ({},{'Origin':'https://evil.example','X-CSRF-Token':self.csrf},{'Origin':self.auth.origin,'X-CSRF-Token':'forged'}):
            self.assertEqual(self.browser.post('/api/auth/register',json=profile(),base_url=self.auth.origin,headers=headers).status_code,403)
        self.assertEqual(self.browser.get('/api/auth/csrf',base_url='https://evil.example').status_code,400)
        self.assertEqual(self.browser.post('/api/auth/register',data=profile(),base_url=self.auth.origin,headers={'Origin':self.auth.origin,'X-CSRF-Token':self.csrf}).status_code,415)

    def test_http_role_injection_and_private_files_are_rejected(self):
        self.assertEqual(self.post('/api/auth/register',{**profile(),'role':'master'}).status_code,400)
        for path in ('/api/master/bootstrap','/static/../var/auth.key','/assets/../platform/var/primoscore.sqlite3'):
            self.assertEqual(self.browser.get(path,base_url=self.auth.origin).status_code,404)
        self.assertEqual(self.post('/api/auth/reset-password',{'token':'x','password':PASSWORD,'purpose':'verify'}).status_code,400)

    def test_http_headers_routes_and_registration_message(self):
        for mode in ('consulente','cliente','master','registrazione','conferma','recupero','profilo'):
            response=self.browser.get('/accesso/'+mode,base_url=self.auth.origin)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        first=self.post('/api/auth/register',profile());second=self.post('/api/auth/register',profile())
        self.assertEqual(first.json,second.json)
        self.assertNotIn('token',json.dumps(first.json))


if __name__=='__main__':unittest.main()
