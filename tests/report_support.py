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


from uuid import uuid4
from primoscore_server.customer import Customer
from primoscore_server.workspace import Workspace
from test_consap_purpose import BASE

class CustomerFixture(AuthFixture):
    def setUp(self):
        super().setUp();self.advisor,self.login,_,self.master=self.active_consultant();self.session=self.login['session_token'];self.ws=Workspace(self.auth);self.customer=Customer(self.auth,local=True)
        self.campaign=self.ws.command(self.session,dict(action='campaign.create',data={'name':'Voucher sintetico','kind':'website'},request_id=str(uuid4())))
        self.code=self.ws.snapshot(self.session)['voucher_lots'][0]['code']

    def payload(self,email='client@example.invalid'):
        return dict(code=self.code,first_name='Nome',last_name='Sintetico',mobile='+39 3330000000',email=email,privacy_accepted=True,service_requested=True,privacy_version='local-test-only',request_id=str(uuid4()))

    def intake(self,email='client@example.invalid'):
        result=self.customer.acquire(self.payload(email),ip=email);return result['guest_token']

    def save(self,guest,answers=None,revision=0,city='Comune sintetico'):
        return self.customer.save(dict(answers=BASE if answers is None else answers,step=5,revision=revision,residence_city=city),guest=guest)

    def configure(self,**d):return self.customer.configure(self.session,d)

