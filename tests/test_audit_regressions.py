import unittest
from unittest.mock import patch
from test_consap_purpose import BASE
from primoscore_core.intake import complete_result
from primoscore_server.public_simulation import evaluate
from primoscore_server.auth import AuthError

FAMILY=dict(istatRegion=3,istatMunicipalityType='3',istatAge0to3=0,istatAge4to10=0,istatAge11to17=0,istatAge18to29=0,istatAge30to59=0,istatAge60to74=1,istatAge75plus=0,householdSize=1,householdEarners=1,children=0)
class AuditRegressions(unittest.TestCase):
    def test_report_payment_matches_approved_assumption(self):
        _,r=evaluate('score',{**BASE,'loanAmount':176000,'propertyPrice':220000,'loanTerm':30})
        self.assertEqual(r['metrics']['indicativePayment']['monthlyAmount'],840)
        self.assertEqual(r['metrics']['estimatedMonthlyPayment'],840)
        self.assertNotIn('4%',r['metrics']['indicativePayment']['notice'])
    def test_wrong_applicant_band_is_rejected(self):
        with self.assertRaises(ValueError):complete_result({**BASE,**FAMILY,'applicantAge':60,'istatAge60to74':0,'istatAge30to59':1})
    def test_two_people_in_same_age_band_require_two_slots(self):
        with self.assertRaises(ValueError):complete_result({**BASE,**FAMILY,'applicantAge':60,'supportRole':'coapplicant','supportAge':62,'supportIncome':2000,'supportInHousehold':'yes'})
    def test_non_household_coapplicant_is_explicit(self):
        complete_result({**BASE,**FAMILY,'applicantAge':60,'supportRole':'coapplicant','supportAge':40,'supportIncome':2000,'supportInHousehold':'no'})
        with self.assertRaises(AuthError):evaluate('max',{**BASE,'supportRole':'coapplicant','supportAge':40,'supportIncome':2000})
    def test_max_report_includes_affordable_payment_and_age_cap(self):
        with patch('primoscore_server.public_simulation.calculate_subsistence',return_value=dict(status='adequate',threshold=1600,referenceYear=2024)):
            _,r=evaluate('max',{**BASE,**FAMILY,'applicantAge':60,'monthlyIncome':3000,'monthlyDebts':200,'loanTerm':30})
        self.assertEqual(r['maximumPayment'],1200)
        self.assertEqual(r['effectiveTerm'],20)
        self.assertEqual(r['maxLoan'],198000)
