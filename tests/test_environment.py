import json
from pathlib import Path
import shutil
import tempfile
import unittest

from signal_trace.validation import read, validate

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = 'scenarios/uc1_deployment_5xx'


class EnvironmentTests(unittest.TestCase):
    def test_environment_validates(self):
        self.assertEqual(validate(ROOT), [])

    def test_deployment_regression_timeline(self):
        base = ROOT / SCENARIO
        deployment = read(base / 'deployments.jsonl')[-1]
        logs = read(base / 'logs.jsonl')
        exceptions = [x for x in logs if x['exception_type'] == 'java.lang.NullPointerException']
        self.assertTrue(exceptions)
        self.assertEqual(deployment['to_version'], '2.3.1')
        for row in exceptions:
            self.assertGreater(row['timestamp'], deployment['timestamp'])
            self.assertEqual((row['service_id'], row['version']), ('checkout-service', '2.3.1'))
        metrics = read(base / 'metrics.jsonl')
        baseline = [x for x in metrics if x['service_id'] == 'checkout-service' and x['timestamp'] < deployment['timestamp']]
        spike = [x for x in metrics if x['service_id'] == 'checkout-service' and x['error_5xx_rate'] > .05]
        self.assertEqual(len(baseline), 5)
        self.assertEqual(len(spike), 9)
        self.assertGreater(min(x['error_5xx_rate'] for x in spike), 10 * max(x['error_5xx_rate'] for x in baseline))
        downstream = [x for x in metrics if x['service_id'] != 'checkout-service']
        self.assertTrue(all(x['error_5xx_rate'] < .05 for x in downstream))
        self.assertTrue(all(x['level'] == 'INFO' for x in logs if x['service_id'] == 'database'))
        truth = read(ROOT / 'evaluation/uc1_deployment_5xx.json')[0]
        self.assertEqual((truth['root_cause'], truth['affected_service'], truth['severity']),
                         ('checkout-service deployment regression', 'checkout-service', 'SEV-2'))

    def corrupted(self, relative, mutate, expected):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['schemas', 'environment', 'scenarios', 'evaluation', 'runbooks']:
                shutil.copytree(ROOT / name, root / name)
            path = root / relative
            rows = read(path)
            mutate(rows)
            path.write_text(''.join(json.dumps(x) + '\n' for x in rows) if path.suffix == '.jsonl' else json.dumps(rows[0]))
            self.assertTrue(any(expected in error for error in validate(root)), expected)

    def test_rejects_invalid_timestamp(self):
        self.corrupted(SCENARIO + '/logs.jsonl', lambda x: x[0].update(timestamp='yesterday'), 'date-time')

    def test_rejects_unknown_service(self):
        self.corrupted(SCENARIO + '/logs.jsonl', lambda x: x[0].update(service_id='unknown'), 'Unknown log service')

    def test_rejects_inconsistent_rate(self):
        self.corrupted(SCENARIO + '/metrics.jsonl', lambda x: x[0].update(error_5xx_rate=.7), 'does not match counts')

    def test_rejects_missing_evidence(self):
        self.corrupted('evaluation/uc1_deployment_5xx.json', lambda x: x[0].update(evidence_ids=['missing']), 'Unknown ground truth evidence')

    def test_rejects_premature_alert(self):
        self.corrupted(SCENARIO + '/incident.json', lambda x: x[0]['alert'].update(timestamp='2026-01-15T10:08:00Z'), 'consecutive elevated')

    def test_rejects_duplicate_ids(self):
        self.corrupted(SCENARIO + '/logs.jsonl', lambda x: x.append(x[0]), 'Duplicate log IDs')


if __name__ == '__main__':
    unittest.main()
