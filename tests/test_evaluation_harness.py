"""Harness tests: scoring mutations, isolation, reports, and aggregation."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from evaluation_harness.runner import ROOT, DEFAULT_CONFIG, execute, run_suite
from evaluation_harness.scoring import aggregate, call_key, grounding, score_case, unsupported_claims


class EvaluationHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.report = run_suite(Path(cls.directory.name))
        cls.executions = {c['case_id']: json.loads((Path(cls.directory.name) / (c['case_id'] + '.trace.json')).read_text())
                          for c in cls.report['cases']}
        cls.truths = {c['case_id']: c['expected'] for c in cls.report['cases']}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def score(self, execution=None, truth=None):
        return score_case('deployment', truth or self.truths['deployment'],
                          execution or self.executions['deployment'])

    def test_five_categories_and_reports(self):
        self.assertEqual(len(self.report['cases']), 5)
        for case in self.report['cases']:
            self.assertIsNotNone(case['actual'])
            for suffix in ('.json', '.trace.json'):
                self.assertTrue((Path(self.directory.name) / (case['case_id'] + suffix)).is_file())
        self.assertTrue((Path(self.directory.name) / 'report.md').is_file())
        self.assertEqual(json.loads((Path(self.directory.name) / 'aggregate.json').read_text())['aggregate'],
                         self.report['aggregate'])

    def test_ground_truth_loaded_after_worker_finishes(self):
        events = []
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.parent == ROOT / 'evaluation/agent':
                events.append(('truth', path.stem))
            return original(path, *args, **kwargs)
        def executor(incident, operational, config):
            case = next(name for name, execution in self.executions.items() if execution['run']['state']['incident']['incident_id'] == incident['incident_id'])
            self.assertNotIn(('truth', case), events)
            self.assertNotIn('root_patterns', json.dumps(operational))
            self.assertNotIn('important_evidence', json.dumps(incident))
            events.append(('finished', case))
            return self.executions[case]
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'read_text', read):
            run_suite(directory, executor=executor)
        for case in self.executions:
            self.assertLess(events.index(('finished', case)), events.index(('truth', case)))

    def test_worker_blocks_ground_truth_and_saved_report_reads(self):
        code = """
import os
from pathlib import Path
from evaluation_harness.worker import deny_evaluation_reads
root = Path.cwd()
deny_evaluation_reads(root)
for path in (root / 'evaluation/agent/deployment.json', root / 'reports/part6_scenarios.json'):
    for opener in (lambda: path.read_text(), lambda: open(path), lambda: os.open(path, os.O_RDONLY)):
        try:
            opener()
        except PermissionError:
            continue
        raise AssertionError('Ground truth was readable')
print('blocked')
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'blocked')

    def test_runtime_has_no_harness_or_truth_imports(self):
        for path in (ROOT / 'signal_trace/agent').glob('*.py'):
            text = path.read_text()
            self.assertNotIn('evaluation_harness', text)
            self.assertNotIn('evaluation/agent', text)

    def test_known_good_scores_and_determinism(self):
        first = self.score()
        self.assertTrue(first['overall_pass'], first)
        self.assertEqual(first, self.score())
        self.assertEqual(first['tool_usage']['decision_count'], 9)

    def test_root_service_and_severity_scores_are_independent(self):
        truth = copy.deepcopy(self.truths['deployment'])
        truth.update(root_patterns=['^impossible$'], affected_services=['other-service'], severity='SEV-1')
        result = self.score(truth=truth)
        self.assertFalse(result['scores']['root_cause'])
        self.assertFalse(result['scores']['affected_services'])
        self.assertFalse(result['scores']['severity'])
        self.assertTrue(result['grounding']['pass'])
        self.assertTrue(result['confidence_quality']['high_confidence_wrong'])

    def test_unknown_reference_and_rewritten_provenance_fail(self):
        for field, value in (('source_id', 'invented'), ('call_ids', ['call-999'])):
            execution = copy.deepcopy(self.executions['deployment'])
            next(iter(execution['run']['state']['evidence'].values()))[field] = value
            self.assertFalse(self.score(execution)['grounding']['pass'])
        execution = copy.deepcopy(self.executions['deployment'])
        execution['run']['result']['primary_hypothesis']['supporting_evidence'].append('invented')
        self.assertFalse(self.score(execution)['grounding']['pass'])

    def test_independent_ledger_rejects_self_consistent_fabrication(self):
        execution = copy.deepcopy(self.executions['deployment'])
        execution['ledger'] = []
        result = self.score(execution)
        self.assertFalse(result['grounding']['pass'])
        self.assertTrue(any('independent tool ledger' in e for e in result['grounding']['reference_errors']))

    def test_unsupported_claims_and_unrecognized_prose_fail_closed(self):
        execution = copy.deepcopy(self.executions['deployment'])
        result, evidence = execution['run']['result'], execution['run']['state']['evidence']
        for description in ('Likely deployment regression in checkout-service after version 9.9.9.',
                            'Local application failure in checkout-service; disk exploded; trigger not established.',
                            'A solar flare caused checkout-service to fail.'):
            result['primary_hypothesis']['description'] = description
            self.assertTrue(unsupported_claims(result, evidence))
        result['summary'] = 'CPU reached 900 percent'
        self.assertTrue(any('Summary' in x for x in unsupported_claims(result, evidence)))

    def test_important_evidence_is_cited_not_merely_collected(self):
        execution = copy.deepcopy(self.executions['deployment'])
        primary = execution['run']['result']['primary_hypothesis']
        primary['supporting_evidence'] = primary['supporting_evidence'][:1]
        self.assertFalse(self.score(execution)['scores']['important_evidence'])

    def test_retries_are_separate_from_repeated_decisions(self):
        case = next(c for c in self.report['cases'] if c['case_id'] == 'missing')
        self.assertEqual(case['tool_usage']['retry_attempts'], 1)
        self.assertEqual(case['tool_usage']['repeated_calls'], 1)
        a = {'name': 'get_metrics', 'arguments': {'service': 'x', 'start_time': '2026-01-15T10:00:00Z', 'end_time': '2026-01-15T10:15:00Z'}}
        b = copy.deepcopy(a)
        b['arguments']['metric_name'] = None
        self.assertEqual(call_key(a), call_key(b))

    def test_early_stop_and_iteration_limit_are_flagged(self):
        truth = copy.deepcopy(self.truths['deployment'])
        truth['required_tools'].append({'tool': 'search_logs', 'service': 'absent-service'})
        self.assertTrue(self.score(truth=truth)['stopping']['too_early'])
        execution = copy.deepcopy(self.executions['deployment'])
        execution['run']['state']['stop_reason'] = 'iteration_limit'
        self.assertTrue(self.score(execution)['stopping']['iteration_limit'])

    def test_high_confidence_missing_and_conflict_flags(self):
        truth = copy.deepcopy(self.truths['deployment'])
        truth['important_evidence'].append('missing-source')
        self.assertTrue(self.score(truth=truth)['confidence_quality']['high_confidence_missing'])
        execution = copy.deepcopy(self.executions['deployment'])
        primary = execution['run']['result']['primary_hypothesis']
        primary['contradicting_evidence'] = [next(iter(execution['run']['state']['evidence']))]
        self.assertTrue(self.score(execution)['confidence_quality']['overconfident_conflict'])

    def test_missing_source_does_not_get_credit_for_absent_human_review(self):
        case = next(c for c in self.report['cases'] if c['case_id'] == 'missing')
        self.assertTrue(case['reliability']['missing_information_reported'])
        self.assertLessEqual(case['confidence'], .6)
        self.assertFalse(case['reliability']['human_review_recommended'])
        self.assertFalse(case['reliability']['pass'])

    def test_aggregate_arithmetic_and_empty_denominators(self):
        a, b = copy.deepcopy(self.score()), copy.deepcopy(self.score())
        a['tool_usage'].update(decision_count=4, repeated_calls=0)
        b['scores'].update(root_cause=False, affected_services=False, severity=False)
        b['grounding']['pass'] = False
        b['unsupported_claims'] = ['invented']
        b['tool_usage'].update(decision_count=6, repeated_calls=2)
        b['confidence_quality']['high_confidence_wrong'] = True
        b['reliability'].update(applicable=True)
        b['reliability']['pass'] = False
        b['overall_pass'] = False
        result = aggregate([a, b])
        for key in ('root_cause_accuracy', 'affected_service_accuracy', 'severity_accuracy', 'grounding_pass_rate', 'unsupported_claim_rate'):
            self.assertEqual(result[key], .5)
        self.assertEqual(result['average_tool_calls'], 5)
        self.assertEqual(result['repeated_call_rate'], .2)
        self.assertEqual(result['high_confidence_wrong_answer_count'], 1)
        self.assertEqual(result['failure_handling_pass_rate'], 0)
        self.assertIsNone(aggregate([a])['failure_handling_pass_rate'])
        with self.assertRaises(ValueError):
            aggregate([])

    def test_execution_failure_is_reported_not_omitted(self):
        def broken(*args):
            raise RuntimeError('Injected worker failure')
        with tempfile.TemporaryDirectory() as directory:
            report = run_suite(directory, executor=broken)
        self.assertEqual(report['aggregate']['cases'], 5)
        self.assertEqual(report['aggregate']['root_cause_accuracy'], 0)
        self.assertTrue(all(c['execution_error'] for c in report['cases']))

    def test_fixture_records_validate_and_labels_are_separate(self):
        from evaluation_harness.worker import FixtureSource
        for name, truth in self.truths.items():
            data = json.loads((ROOT / 'fixtures/agent_scenarios' / name / 'operational.json').read_text())
            source = FixtureSource({**data, 'faults': {}})
            rows = source.logs() + source.metrics() + source.deployments()
            ids = {getattr(r, key, None) for r in rows for key in ('log_id', 'metric_id', 'deployment_id')}
            self.assertLessEqual(set(truth['important_evidence']), ids)
            self.assertNotIn('root_cause', data)
            self.assertTrue(source.runbooks())



class BaselineComparisonTests(unittest.TestCase):
    def test_new_quality_regression_is_detected(self):
        from evaluation_harness.runner import compare_baseline, execution_failure
        truth = json.loads((ROOT / 'evaluation/agent/deployment.json').read_text())
        case = execution_failure('deployment', truth, 'error')
        baseline = {'cases': [copy.deepcopy(case)], 'aggregate': aggregate([case])}
        baseline['cases'][0]['scores']['root_cause'] = True
        current = {'cases': [case], 'aggregate': aggregate([case])}
        comparison = compare_baseline(current, baseline)
        self.assertFalse(comparison['pass'])
        self.assertIn('deployment: root_cause', comparison['regressions'])
        self.assertTrue(compare_baseline(current, current)['pass'])

    def test_omitted_cases_are_regressions(self):
        from evaluation_harness.runner import compare_baseline
        result = compare_baseline({'cases': [], 'aggregate': {}},
                                  {'cases': [{'case_id': 'missing'}], 'aggregate': {}})
        self.assertFalse(result['pass'])
        self.assertEqual(result['missing_cases'], ['missing'])
