import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from primoscore_core import score_engine
from primoscore_core.questionnaire import validate_complete_answers, validate_partial_step


class ExtractedCoreTest(unittest.TestCase):
    def test_null_istat_payloads_degrade_to_unavailable(self):
        answers=json.loads((ROOT/'tests/score-reference.json').read_text())['cases'][0]['answers']
        for payload in ({'elements':None},{'elements':[{'SOGLIA':None}]},{'elements':[{'SOGLIA':'NaN'}]}):
            with self.subTest(payload=payload),patch.object(score_engine,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
                result=score_engine.calculate_score(answers)
            self.assertEqual(result['metrics']['subsistence']['status'],'unavailable')

    def test_results_match_pinned_ciceroev_reference(self):
        reference = json.loads((ROOT / 'tests/score-reference.json').read_text())
        self.assertTrue(reference['synthetic'])
        for case in reference['cases']:
            with self.subTest(case=case['name']):
                def lookup(*args, **kwargs):
                    mode = case['istat']
                    if mode == 'unexpected':
                        self.fail('A scenario without ISTAT inputs attempted a network request')
                    if mode == 'failure':
                        raise OSError('Synthetic offline response')
                    payload = {} if mode == 'malformed' else {'elements': [{'SOGLIA': str(mode) + ',00'}]}
                    return io.BytesIO(json.dumps(payload).encode())
                with patch.object(score_engine, 'urlopen', side_effect=lookup):
                    actual = score_engine.calculate_score(case['answers'])
                expected = dict(case['expected'])
                expected['engineVersion'] = 'primoscore-mutuoscore-1.7'
                if expected['metrics']['subsistence']['status'] == 'below_threshold':
                    expected['totalScore'] = min(expected['totalScore'], 59)
                    expected['classification'] = score_engine.classify(score_engine.load_constitution()['classification'], expected['totalScore'])
                if case['answers'].get('purpose') != 'first_home':
                    expected['consap'] = None
                self.assertEqual(actual, expected)

    def test_copied_sources_have_not_changed(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/check_source.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_integrity_check_rejects_modified_engine(self):
        with tempfile.TemporaryDirectory(prefix='primoscore-integrity-') as folder:
            target = Path(folder)
            (target / 'tools').mkdir()
            (target / 'tools/check_source.py').write_bytes((ROOT / 'tools/check_source.py').read_bytes())
            (target / 'source-manifest.json').write_bytes((ROOT / 'source-manifest.json').read_bytes())
            manifest = json.loads((ROOT / 'source-manifest.json').read_text())
            for item in manifest['extracted']:
                destination = target / item['destination']
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes((ROOT / item['destination']).read_bytes())
            changed = target / 'primoscore_core/score_engine.py'
            changed.write_bytes(changed.read_bytes() + b'\n# simulated upstream/local drift\n')
            result = subprocess.run([sys.executable, str(target / 'tools/check_source.py')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Pacchetto modificato: primoscore_core/score_engine.py', result.stdout)

    def test_invalid_draft_answers_are_rejected(self):
        cleaned, errors = validate_partial_step(2, {'monthlyIncome': -1, 'jobType': 'invented', 'applicantAge': 32})
        self.assertEqual(cleaned, {'applicantAge': 32})
        self.assertEqual(set(errors), {'monthlyIncome', 'jobType'})

    def test_cleared_draft_values_remain_absent(self):
        cleaned, errors = validate_partial_step(2, {'monthlyIncome': '', 'applicantAge': 32})
        self.assertEqual(cleaned, {'applicantAge': 32})
        self.assertFalse(errors)

    def test_support_and_isee_required_fields(self):
        reference = json.loads((ROOT / 'tests/score-reference.json').read_text())
        answers = {**reference['cases'][0]['answers'], 'supportRole': 'coapplicant'}
        del answers['iseeBand']
        self.assertEqual(set(validate_complete_answers(answers)), {'supportAge', 'supportIncome', 'iseeBand'})

    def test_import_has_no_application_or_storage_side_effects(self):
        result = subprocess.run([sys.executable, '-c', "import sys; import primoscore_core; assert 'flask' not in sys.modules; assert not any(n == 'server' or n.startswith('server.') for n in sys.modules)"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        allowed = {'intake.py', '__init__.py', 'score_engine.py', 'questionnaire.py', 'mutuoscore_constitution.json', '__pycache__'}
        self.assertLessEqual({p.name for p in (ROOT / 'primoscore_core').iterdir()}, allowed)


if __name__ == '__main__':
    unittest.main()
