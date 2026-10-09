import unittest
from unittest.mock import patch
from primoscore_core import score_engine
from test_consap_purpose import BASE

class PurchaseFundingTest(unittest.TestCase):
    def score(self, **changes):
        with patch.object(score_engine,'calculate_subsistence',return_value={'status':'adequate'}):
            return score_engine.calculate_score({**BASE,'propertyPrice':100000,'loanAmount':80000,'savings':10000,**changes})
    def test_missing_down_payment_reduces_score_and_quantifies_gap(self):
        r=self.score()
        self.assertLessEqual(r['totalScore'],49)
        self.assertEqual(r['metrics']['purchaseFunding']['shortfall'],10000)
        self.assertIn('10.000 euro',r['warnings'][0]);self.assertIn('spese',r['warnings'][0])
    def test_covering_price_alone_does_not_cover_costs(self):
        r=self.score(savings=20000)
        self.assertLessEqual(r['totalScore'],59)
        self.assertEqual(r['metrics']['purchaseFunding']['shortfall'],0)
        self.assertEqual(r['metrics']['purchaseFunding']['availableForCosts'],0)
        self.assertIn('non rimane disponibilità',r['warnings'][0])
    def test_surplus_is_reported_without_claiming_costs_are_covered(self):
        r=self.score(savings=30000)
        self.assertGreater(r['totalScore'],59)
        self.assertEqual(r['metrics']['purchaseFunding']['availableForCosts'],10000)
        self.assertFalse(r['metrics']['purchaseFunding']['costsIncluded'])
    def test_other_stricter_caps_remain(self):
        self.assertLessEqual(self.score(creditHistory='active')['totalScore'],49)
    def test_both_purchase_purposes_are_checked(self):
        self.assertLessEqual(self.score(purpose='second_home')['totalScore'],49)
    def test_other_operations_are_excluded(self):
        for purpose in ('renovation','surrogation'):
            self.assertIsNone(self.score(purpose=purpose)['metrics']['purchaseFunding'])
    def test_twenty_percent_does_not_hide_larger_down_payment(self):
        r=self.score(propertyPrice=250000,loanAmount=180000,savings=55000)
        self.assertEqual(r['metrics']['purchaseFunding']['shortfall'],15000)
        self.assertFalse(any('almeno il 20%' in s for s in r['strengths']))
