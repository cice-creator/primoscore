import json
from urllib.parse import urlsplit,parse_qs
from test_customer import CustomerTest,BASE
from primoscore_server.auth import AuthError
from primoscore_server.notifications import send_operational
from primoscore_server.operations import Operations

class ReportDeliveryTest(CustomerTest):
    def delivery(self):
        with self.db.transaction() as c:
            row=c.execute("SELECT payload_encrypted FROM service_mail WHERE event_key LIKE 'report:%' ORDER BY rowid DESC LIMIT 1").fetchone()
        payload=json.loads(self.auth.cipher.decrypt(row[0].encode()))
        return payload,parse_qs(urlsplit(payload['url']).fragment)['token'][0]

    def test_mailbox_verification_gates_report(self):
        guest=self.intake();self.save(guest)
        sent=self.customer.complete({'revision':1},guest=guest)
        self.assertNotIn('result',sent)
        self.assertIsNone(self.customer.get(guest=guest)['result'])
        payload,token=self.delivery()
        self.assertNotIn('totalScore',payload);self.assertNotIn('answers',payload)
        with self.assertRaises(AuthError):self.customer.report(token)
        self.assertEqual(self.customer.report(token,verify=True)['result']['totalScore'],49)
        self.assertEqual(self.customer.get(guest=guest)['result']['totalScore'],49)
        self.assertEqual(self.customer.report(token)['result']['totalScore'],49)

    def test_correction_revoke_resend_and_revision(self):
        guest=self.intake();self.save(guest);self.customer.complete({'revision':1},guest=guest)
        _,old=self.delivery()
        self.customer.complete({'revision':1,'email':'corrected@example.invalid'},guest=guest)
        with self.assertRaises(AuthError):self.customer.report(old,verify=True)
        payload,token=self.delivery();self.assertEqual(payload['to'],'corrected@example.invalid')
        self.assertEqual(self.customer.get(guest=guest)['client']['email'],'client@example.invalid')
        self.customer.report(token,verify=True)
        self.assertEqual(self.customer.get(guest=guest)['client']['email'],'corrected@example.invalid')
        self.save(guest,{**BASE,'savings':60000},revision=1)
        with self.assertRaises(AuthError):self.customer.report(token)

    def test_pending_account_mail_is_sent_and_revoked_mail_cancelled(self):
        guest=self.intake();self.save(guest);self.customer.complete({'revision':1},guest=guest)
        self.customer.complete({'revision':1},guest=guest)
        class Mailer:
            def __init__(self):self.sent=[]
            def send(self,payload):self.sent.append(payload)
        mailer=Mailer();send_operational(self.auth,mailer)
        self.assertEqual(len([p for p in mailer.sent if p['purpose']=='report']),1)
        _,token=self.delivery();self.time+=7*86400+1
        with self.assertRaises(AuthError):self.customer.report(token,verify=True)

    def test_erasure_revokes_report(self):
        guest=self.intake();self.save(guest);self.customer.complete({'revision':1},guest=guest)
        _,token=self.delivery();client=self.customer.get(guest=guest)['client']
        Operations(self.auth).erase(self.session,dict(client_id=client['id'],revision=client['revision'],confirmation='ANONIMIZZA'))
        with self.assertRaises(AuthError):self.customer.report(token,verify=True)
