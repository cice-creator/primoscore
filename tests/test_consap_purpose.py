import json
from pathlib import Path
import unittest
from primoscore_core.intake import clean_answers, complete_result
from primoscore_core.score_engine import calculate_score
from primoscore_core.questionnaire import validate_complete_answers

BASE = {'creditHistory': 'none', 'applicantAge': 32, 'monthlyIncome': 3000, 'annualPayments': 13, 'jobType': 'permanent', 'householdSize': 2, 'children': 0, 'householdEarners': 2, 'savings': 55000, 'monthlyDebts': 100, 'savingHabit': 'yes', 'propertyPrice': 250000, 'loanAmount': 180000, 'loanTerm': 25, 'purpose': 'first_home', 'otherHome': 'no', 'supportRole': 'none', 'iseeBand': 'under_or_equal_40k'}

class ConsapPurposeTest(unittest.TestCase):
    def test_other_purposes_complete_without_fund_answers(self):
        for purpose in ('renovation', 'surrogation', 'second_home'):
            for age in (30, 45):
                with self.subTest(purpose=purpose, age=age):
                    answers = {**BASE, 'purpose': purpose, 'applicantAge': age}
                    for key in ('otherHome', 'iseeBand', 'iseeUnknown'):
                        answers.pop(key, None)
                    self.assertEqual(validate_complete_answers(answers), [])
                    self.assertIsNone(complete_result(answers)['consap'])
                    self.assertIsNone(calculate_score(answers)['consap'])
                    answers['propertyFound'] = 'no'
                    answers.pop('loanAmount'); answers.pop('propertyPrice')
                    self.assertIsNone(complete_result(answers)['consap'])

    def test_changing_purpose_removes_previous_fund_answers(self):
        answers = clean_answers({**BASE, 'purpose': 'renovation', 'iseeUnknown': 'yes'})
        for key in ('otherHome', 'iseeBand', 'iseeUnknown'):
            self.assertNotIn(key, answers)
        self.assertIsNone(complete_result(answers)['consap'])

    def test_first_home_preserves_fund_and_required_answers(self):
        self.assertIsNotNone(complete_result(BASE)['consap'])
        answers = {k: v for k, v in BASE.items() if k not in ('iseeBand', 'otherHome')}
        self.assertEqual(set(validate_complete_answers(answers)), {'iseeBand', 'otherHome'})
