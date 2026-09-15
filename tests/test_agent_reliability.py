"""Fault injection tests for bounded, evidence-preserving investigations."""
from datetime import datetime
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_trace.agent import Investigator, OfflineReferenceModel
from signal_trace.agent.models import Assessment, Hypothesis, InvestigationResult, ToolCall
from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.incident = IncidentRequest.model_validate_json(
            Path('scenarios/uc1_deployment_5xx/incident.json').read_text())
        self.tools = ToolLayer()

    def run_agent(self, model=None, **kwargs):
        return Investigator(model or OfflineReferenceModel(), self.tools, **kwargs).run(self.incident)

    def test_transient_timeout_retries_once_and_recovers(self):
        original = self.tools.get_metrics
        attempts = []
        def flaky(**arguments):
            attempts.append(arguments)
            if len(attempts) == 1:
                raise TimeoutError('Temporary timeout')
            return original(**arguments)
        with patch.object(self.tools, 'get_metrics', side_effect=flaky):
            run = self.run_agent()
        self.assertEqual(run.state.tool_results[0].attempts, 2)
        self.assertEqual(run.result.outcome, 'sufficient_evidence')
        self.assertTrue(any(i.kind == 'TimeoutError' for i in run.state.reliability_issues))

    def test_permanent_failure_is_not_retried_and_reduces_confidence(self):
        with patch.object(self.tools, 'get_recent_deployments', side_effect=PermissionError('Denied')) as call:
            run = self.run_agent()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(run.result.outcome, 'insufficient_evidence')
        self.assertLessEqual(run.result.confidence, .6)
        self.assertTrue(any('get_recent_deployments' in g for g in run.state.missing_information))
        self.assertTrue(run.state.evidence)

    def test_hung_tool_returns_without_waiting_or_overlapping_retry(self):
        release = threading.Event()
        def hung(**kwargs):
            release.wait(3)
            return []
        try:
            with patch.object(self.tools, 'get_metrics', side_effect=hung) as call:
                start = time.monotonic()
                run = self.run_agent(tool_timeout=.02, max_iterations=1)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(call.call_count, 1)
            self.assertEqual(run.state.tool_results[0].status, 'failed')
            self.assertFalse(run.state.evidence)
        finally:
            release.set()

    def test_exhausted_transient_failure_is_bounded(self):
        with patch.object(self.tools, 'get_metrics', side_effect=ConnectionError('Offline')) as call:
            run = self.run_agent(max_iterations=1)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(run.result.confidence, 0)
        self.assertEqual(len(run.state.reliability_issues), 2)

    def test_invalid_response_is_atomic_and_never_retried(self):
        record = self.tools.get_metrics(service=self.incident.alert.service_id,
            start_time=datetime.fromisoformat(self.incident.observation_start),
            end_time=datetime.fromisoformat(self.incident.observation_end))[0]
        for value in ([record, {'unexpected': True}], {'value': 3}, [record.model_copy(update={'value': float('nan')})]):
            with self.subTest(value=value), patch.object(self.tools, 'get_metrics', return_value=value) as call:
                run = self.run_agent(max_iterations=1)
                self.assertEqual(call.call_count, 1)
                self.assertFalse(run.state.evidence)
                self.assertEqual(run.state.tool_results[0].status, 'invalid')

    def test_missing_data_is_explicit_and_not_invented(self):
        for value in (None, []):
            with self.subTest(value=value), patch.object(self.tools, 'get_metrics', return_value=value):
                run = self.run_agent(max_iterations=1)
                self.assertEqual(run.state.tool_results[0].status, 'empty')
                self.assertIsNone(run.result.root_cause)
                self.assertEqual(run.result.confidence, 0)
                self.assertTrue(run.state.missing_information)
                self.assertFalse(run.result.evidence_ids)

    def test_model_invalid_selection_repairs_once(self):
        class Repairing(OfflineReferenceModel):
            attempts = 0
            def select_tool(self, state):
                self.attempts += 1
                if self.attempts == 1:
                    return {'name': 'invented_tool'}
                return super().select_tool(state)
        model = Repairing()
        run = self.run_agent(model, max_iterations=1)
        self.assertEqual(model.attempts, 2)
        self.assertEqual(run.state.tool_results[0].status, 'success')
        self.assertTrue(run.state.reliability_issues)

    def test_invalid_final_output_falls_back_with_exact_evidence(self):
        class Broken(OfflineReferenceModel):
            def finalize(self, state):
                return {'root_cause': 'invented'}
        run = self.run_agent(Broken())
        self.assertEqual(run.result.outcome, 'insufficient_evidence')
        self.assertEqual(run.state.stop_reason, 'model_failure')
        self.assertTrue(any('finalize' in g for g in run.result.missing_information))
        self.assertEqual(run.result.supporting_evidence, list(run.state.evidence.values()))
        InvestigationResult.model_validate_json(run.result.model_dump_json())

    def test_model_timeout_stops_with_safe_result(self):
        release = threading.Event()
        class Hanging(OfflineReferenceModel):
            def select_tool(self, state):
                release.wait(3)
                return super().select_tool(state)
        try:
            run = self.run_agent(Hanging(), model_timeout=.02)
            self.assertEqual(run.state.stop_reason, 'model_failure')
            self.assertEqual(run.result.outcome, 'insufficient_evidence')
        finally:
            release.set()

    def test_duplicate_defaults_are_normalized_and_not_executed(self):
        class Repeating(OfflineReferenceModel):
            def select_tool(self, state):
                args = {'service': state.incident.alert.service_id,
                        'start_time': state.incident.observation_start,
                        'end_time': state.incident.observation_end}
                if state.tool_results:
                    args['metric_name'] = None
                return ToolCall(name='get_metrics', arguments=args, reason='Measure')
        with patch.object(self.tools, 'get_metrics', wraps=self.tools.get_metrics) as call:
            run = self.run_agent(Repeating(), max_iterations=100)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(run.state.stop_reason, 'no_progress')
        self.assertEqual(run.state.tool_results[-1].status, 'skipped')
        self.assertEqual(run.state.iteration_count, 2)

    def test_budget_is_absolute(self):
        run = self.run_agent(max_iterations=1)
        self.assertEqual(run.state.iteration_count, 1)
        self.assertEqual(run.state.stop_reason, 'iteration_limit')
        self.assertEqual(run.result.outcome, 'insufficient_evidence')

    def test_conflict_cannot_be_silently_dropped_or_claimed_sufficient(self):
        class Conflicting(OfflineReferenceModel):
            def assess(self, state):
                rows = list(state.evidence)
                if len(rows) < 2:
                    return super().assess(state)
                return Assessment(hypotheses=[Hypothesis(hypothesis_id='conflict',
                    description='Provisional local failure', confidence=.99,
                    supporting_evidence=[rows[0]],
                    contradicting_evidence=[rows[1]] if state.iteration_count == 1 else [])],
                    primary_hypothesis_id='conflict', sufficient_evidence=True,
                    rationale='Conflicting measurements need investigation')
        run = self.run_agent(Conflicting(), max_iterations=2)
        h = run.result.primary_hypothesis
        self.assertTrue(h.contradicting_evidence)
        self.assertLessEqual(h.confidence, .6)
        self.assertEqual(run.result.outcome, 'insufficient_evidence')
        self.assertTrue(set(h.supporting_evidence + h.contradicting_evidence) <= set(run.result.evidence_ids))

    def test_single_tool_cannot_claim_sufficient_evidence(self):
        class Overconfident(OfflineReferenceModel):
            def assess(self, state):
                if not state.evidence:
                    return super().assess(state)
                return Assessment(hypotheses=[Hypothesis(hypothesis_id='weak',
                    description='Tentative cause', confidence=.99,
                    supporting_evidence=[next(iter(state.evidence))])],
                    primary_hypothesis_id='weak', sufficient_evidence=True,
                    rationale='Unsupported sufficiency claim')
        run = self.run_agent(Overconfident(), max_iterations=1)
        self.assertFalse(run.state.assessment.sufficient_evidence)
        self.assertLessEqual(run.result.confidence, .6)
        self.assertEqual(run.result.outcome, 'insufficient_evidence')

    def test_configuration_rejects_unbounded_timeouts(self):
        for value in (0, -1, float('inf'), float('nan'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Investigator(OfflineReferenceModel(), tool_timeout=value)


if __name__ == '__main__':
    unittest.main()
