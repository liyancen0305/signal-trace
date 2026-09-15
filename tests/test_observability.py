import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from signal_trace.agent import Investigator, OfflineReferenceModel
from signal_trace.agent.observability import InvestigationTracer
from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer

class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.incident = IncidentRequest.model_validate_json(Path('scenarios/uc1_deployment_5xx/incident.json').read_text())
    def test_normal_trace_complete_and_ordered(self):
        run = Investigator(OfflineReferenceModel(), ToolLayer()).run(self.incident)
        trace = run.trace
        self.assertEqual(trace['incident_id'], self.incident.incident_id)
        self.assertTrue(trace['started_at'] and trace['ended_at'] and trace['duration_ms'] >= 0)
        self.assertEqual([x['name'] for x in trace['tool_calls']], [r.call.name for r in run.state.tool_results])
        self.assertTrue(trace['evidence_added'])
        self.assertTrue(trace['hypothesis_changes'])
        self.assertEqual(trace['stopping_reason'], run.state.stop_reason)
        self.assertEqual(trace['final_result'], run.result.model_dump(mode='json'))
        self.assertEqual(trace['metrics']['tool_call_count'], len(run.state.tool_results))
        self.assertEqual(trace['metrics']['llm_call_count'], len(trace['llm_calls']))
        self.assertEqual(trace['metrics']['iteration_count'], run.state.iteration_count)
        self.assertEqual(len(trace['metrics']['tool_call_latency_ms']), len(trace['tool_calls']))
    def test_failure_and_retry_trace(self):
        tools = ToolLayer()
        original = tools.get_metrics
        calls = []
        def flaky(**kwargs):
            calls.append(1)
            if len(calls) == 1: raise TimeoutError('temporary')
            return original(**kwargs)
        with patch.object(tools, 'get_metrics', side_effect=flaky):
            run = Investigator(OfflineReferenceModel(), tools).run(self.incident)
        trace = run.trace
        self.assertGreaterEqual(trace['metrics']['retry_count'], 1)
        self.assertTrue(trace['retries'])
        self.assertTrue(any(i['kind'] == 'tool' for i in trace['retries']))
        self.assertEqual(trace['stopping_reason'], run.state.stop_reason)
        self.assertEqual(trace['final_result']['incident_id'], self.incident.incident_id)
        self.assertEqual(trace['metrics']['failure_count'], 0)
    def test_permanent_failure_metrics(self):
        tools = ToolLayer()
        with patch.object(tools, 'get_recent_deployments', side_effect=PermissionError('denied')):
            run = Investigator(OfflineReferenceModel(), tools).run(self.incident)
        metrics = run.trace['metrics']
        self.assertGreaterEqual(metrics['failure_count'], 1)
        self.assertEqual(metrics['retry_count'], 0)
        self.assertTrue(run.trace['failures'])
        self.assertEqual(metrics['tool_call_count'], len(run.state.tool_results))
    def test_redaction_and_custom_recorder(self):
        tracer = InvestigationTracer()
        self.assertEqual(tracer.snapshot()['metrics']['estimated_model_cost'], None)
        self.assertEqual(tracer.snapshot()['metrics']['token_usage'], None)
        self.assertEqual(__import__('signal_trace.agent.observability', fromlist=['redact']).redact({'api_token':'abc','nested':{'password':'x'}}), {'api_token':'[REDACTED]','nested':{'password':'[REDACTED]'}})
        run = Investigator(OfflineReferenceModel(), ToolLayer(), tracer=tracer).run(self.incident)
        self.assertIs(run.trace['incident_id'], self.incident.incident_id)
    def test_observability_does_not_change_result(self):
        a = Investigator(OfflineReferenceModel(), ToolLayer()).run(self.incident)
        b = Investigator(OfflineReferenceModel(), ToolLayer(), tracer=InvestigationTracer()).run(self.incident)
        self.assertEqual(a.result, b.result)
        self.assertEqual([r.call for r in a.state.tool_results], [r.call for r in b.state.tool_results])
if __name__ == '__main__': unittest.main()

