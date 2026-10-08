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

    def test_qr_url_preserved_and_lead_attributed(self):
        self.auth.origin='http://127.0.0.1:8767'
        app=create_app(self.db,self.key,self.auth.origin,local=True,auth=self.auth)
        client=app.test_client();origin=self.auth.origin
        self.assertEqual(client.get('/v/'+self.code,base_url=origin).status_code,200)
        self.assertIn(b'simulation.js',client.get('/v/'+self.code,base_url=origin).data)
        csrf=client.get('/api/auth/csrf',base_url=origin).json['csrf']
        headers={'Origin':origin,'X-CSRF-Token':csrf}
        answers={**BASE,'applicantAge':60,'loanTerm':30,'monthlyIncome':3000,'monthlyDebts':200,'supportRole':'none'}
        with patch('primoscore_server.public_simulation.calculate_subsistence',return_value=dict(status='adequate',threshold=1600,referenceYear=2024)):
            estimate=client.post('/api/public-simulation/estimate',base_url=origin,headers=headers,json=dict(code=self.code,mode='max',answers=answers))
            self.assertEqual(estimate.status_code,200,estimate.json)
            lead=client.post('/api/public-simulation/lead',base_url=origin,headers=headers,json=dict(mode='max',answers=answers,contact=self.payload(),residence_city='Comune sintetico'))
            self.assertEqual(lead.status_code,200,lead.json)
        state=client.get('/api/customer',base_url=origin).json
        self.assertEqual(state['partner_label'],'Voucher sintetico')
        self.assertEqual(state['result']['effectiveTerm'],20)
        self.assertEqual(state['result']['simulationMode'],'max')
        self.assertEqual(state['client']['lot_id'],self.ws.snapshot(self.session)['voucher_lots'][0]['id'])
        rejected=client.post('/api/public-simulation/estimate',base_url=origin,json=dict(code=self.code,mode='max',answers=answers))
        self.assertEqual(rejected.status_code,403)

if __name__=='__main__':unittest.main()
