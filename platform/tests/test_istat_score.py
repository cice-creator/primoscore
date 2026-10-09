import unittest
from unittest.mock import patch
from primoscore_core import score_engine
from test_consap_purpose import BASE

class IstatScoreTest(unittest.TestCase):
    def score(self,status,answers=None):
        with patch.object(score_engine,'calculate_subsistence',return_value={'status':status,'message':'Verifica da completare.'}):
            return score_engine.calculate_score(answers or BASE)

    def test_below_threshold_limits_otherwise_good_score(self):
        good=self.score('adequate'); low=self.score('below_threshold')
        self.assertGreater(good['totalScore'],59)
        self.assertEqual(low['totalScore'],59)
        self.assertEqual(low['rawScore'],good['rawScore'])
        self.assertTrue(any('soglia ISTAT' in w for w in low['warnings']))

    def test_stricter_existing_limits_are_preserved(self):
        self.assertEqual(self.score('below_threshold',{**BASE,'savings':1000})['totalScore'],49)

    def test_attention_and_missing_data_do_not_apply_failure_cap(self):
        good=self.score('adequate')['totalScore']
        for status in ('attention','unavailable'):
            self.assertEqual(self.score(status)['totalScore'],good)

    def test_debt_ratio_failure_remains_independent_of_istat(self):
        result=self.score('adequate',{**BASE,'monthlyDebts':2000})
        self.assertLessEqual(result['totalScore'],59)
