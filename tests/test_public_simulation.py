from unittest.mock import patch
import unittest
from report_support import CustomerFixture
from test_consap_purpose import BASE
from primoscore_server.public_simulation import evaluate
from primoscore_server.web import create_app
from primoscore_server.auth import AuthError

class PublicSimulationTest(CustomerFixture):
    def test_age_cap_and_limits(self):
        answers={**BASE,'applicantAge':60,'loanTerm':30,'monthlyIncome':3000,'monthlyDebts':200,'supportRole':'none'}
        with patch('primoscore_server.public_simulation.calculate_subsistence',return_value=dict(status='adequate',threshold=1600,referenceYear=2024)):
            cleaned,result=evaluate('max',answers)
        self.assertEqual(result['effectiveTerm'],20)
        self.assertNotIn('loanAmount',cleaned)
        self.assertEqual(result['maxLoan'],198000)
        with patch('primoscore_server.public_simulation.calculate_subsistence',return_value=dict(status='unavailable')):
            with self.assertRaises(AuthError):evaluate('max',answers)

    def test_registration_then_email_verification_for_both_scenarios(self):
        import json
        from urllib.parse import urlsplit,parse_qs
        self.auth.origin='http://127.0.0.1:8767'
        app=create_app(self.db,self.key,self.auth.origin,local=True,auth=self.auth)
        origin=self.auth.origin
        for mode in ('score','max'):
            with self.subTest(mode=mode):
                client=app.test_client()
                self.assertIn(b'simulation.js',client.get('/v/'+self.code,base_url=origin).data)
                csrf=client.get('/api/auth/csrf',base_url=origin).json['csrf']
                headers={'Origin':origin,'X-CSRF-Token':csrf}
                contact={**self.payload(email=mode+'@example.invalid'),'simulation_mode':mode,'residence_city':'Comune sintetico'}
                acquired=client.post('/api/customer/intake',base_url=origin,headers=headers,json=contact)
                self.assertEqual(acquired.status_code,200,acquired.json)
                state=client.get('/api/customer',base_url=origin).json
                self.assertIsNone(state['result'])
                self.assertEqual(state['questionnaire']['simulation_mode'],mode)
                self.assertEqual(state['partner_label'],'Voucher sintetico')
                self.assertEqual(state['client']['residence_city'],'Comune sintetico')
                self.assertEqual(client.get('/cliente/simulazione',base_url=origin).status_code,200)
                # The contact is already present even if no questionnaire is completed.
                self.assertIsNone(self.customer.advisor(self.session,client_id=state['client']['id'])['result'])
                answers={**BASE,'applicantAge':60,'loanTerm':30,'monthlyIncome':3000,'monthlyDebts':200,'supportRole':'none'}
                if mode=='max':
                    answers['propertyFound']='no';answers.pop('loanAmount',None);answers.pop('propertyPrice',None)
                saved=client.post('/api/customer/save',base_url=origin,headers=headers,json=dict(answers=answers,step=2,revision=0,residence_city='Comune sintetico'))
                self.assertEqual(saved.status_code,200,saved.json)
                with patch('primoscore_server.public_simulation.calculate_subsistence',return_value=dict(status='adequate',threshold=1600,referenceYear=2024)):
                    sent=client.post('/api/customer/complete',base_url=origin,headers=headers,json=dict(revision=1))
                self.assertEqual(sent.status_code,200,sent.json)
                self.assertNotIn('result',sent.json)
                pending=client.get('/api/customer',base_url=origin).json
                self.assertTrue(pending['assessment_completed'])
                self.assertIsNone(pending['result'])
                with self.db.transaction() as c:
                    row=c.execute("SELECT payload_encrypted FROM service_mail WHERE event_key LIKE 'report:%' ORDER BY rowid DESC LIMIT 1").fetchone()
                payload=json.loads(self.auth.cipher.decrypt(row[0].encode()))
                self.assertNotIn('answers',payload);self.assertNotIn('totalScore',payload)
                token=parse_qs(urlsplit(payload['url']).fragment)['token'][0]
                self.assertEqual(client.get('/api/customer/report',base_url=origin).status_code,403)
                confirmed=client.post('/api/customer/report/open',base_url=origin,headers=headers,json=dict(token=token))
                self.assertEqual(confirmed.status_code,200,confirmed.json)
                result=confirmed.json['result']
                self.assertIsNotNone(client.get('/api/customer',base_url=origin).json['result'])
                if mode=='max':
                    self.assertEqual(result['effectiveTerm'],20)
                    self.assertEqual(result['maxLoan'],198000)
                    # Resending a stored report must not depend on a fresh ISTAT request.
                    with patch('primoscore_server.public_simulation.calculate_subsistence',return_value=dict(status='unavailable')):
                        resent=client.post('/api/customer/complete',base_url=origin,headers=headers,json=dict(revision=1,email='corrected-max@example.invalid'))
                    self.assertEqual(resent.status_code,200,resent.json)
                    self.assertIsNone(client.get('/api/customer',base_url=origin).json['result'])
                    self.assertEqual(client.post('/api/customer/report/open',base_url=origin,headers=headers,json=dict(token=token)).status_code,403)

    def test_old_anonymous_result_endpoints_no_longer_disclose_results(self):
        self.auth.origin='http://127.0.0.1:8767'
        app=create_app(self.db,self.key,self.auth.origin,local=True,auth=self.auth)
        client=app.test_client();origin=self.auth.origin
        csrf=client.get('/api/auth/csrf',base_url=origin).json['csrf']
        headers={'Origin':origin,'X-CSRF-Token':csrf}
        for endpoint in ('estimate','lead'):
            response=client.post('/api/public-simulation/'+endpoint,base_url=origin,headers=headers,json=dict(code=self.code,mode='max',answers=BASE))
            self.assertEqual(response.status_code,410)
            self.assertNotIn('result',response.json)
        self.assertEqual(client.get('/cliente/simulazione',base_url=origin).status_code,401)

if __name__=='__main__':unittest.main()
