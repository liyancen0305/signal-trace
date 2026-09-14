"""Offline workflow tests; counterfactuals modify UC1 evidence in memory only."""
import builtins
import inspect
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from signal_trace.agent import Investigator, OfflineReferenceModel, OllamaModel
from signal_trace.agent.models import Assessment, Hypothesis, InvestigationResult, ToolCall
from signal_trace.agent.runtime import configured_investigator
from signal_trace.api.app import create_app
from signal_trace.config import Settings
from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer
from signal_trace.tools.source import SyntheticSource

ROOT = Path(__file__).resolve().parents[1]


class MemorySource:
    def __init__(self):
        source = SyntheticSource()
        self.data = {name: getattr(source, name)() for name in
                     ('logs', 'metrics', 'deployments', 'services', 'edges', 'runbooks')}

    def __getattr__(self, name):
        return lambda: self.data[name]


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.incident = IncidentRequest.model_validate_json(
            (ROOT / 'scenarios/uc1_deployment_5xx/incident.json').read_text())
        self.source = MemorySource()

    def run_agent(self, model=None, limit=12):
        return Investigator(model or OfflineReferenceModel(), ToolLayer(self.source), limit).run(self.incident)

    def test_end_to_end_state_evidence_hypotheses_and_result(self):
        run = self.run_agent()
        state, result = run.state, run.result
        self.assertEqual(result.affected_services, ['checkout-service'])
        self.assertEqual(result.primary_hypothesis.hypothesis_id, 'deployment-regression')
        self.assertIn('discount.code is null', result.root_cause)
        self.assertEqual(result.severity, 'SEV-2')
        self.assertEqual(state.stop_reason, 'sufficient_evidence')
        self.assertEqual(state.iteration_count, 9)
        self.assertEqual(len(state.history), state.iteration_count + 1)
        self.assertEqual(state.history[0].evidence_count, 0)
        self.assertEqual(state.history[0].assessment.hypotheses, [])
        self.assertTrue(state.history[0].assessment.missing_information)
        counts = [step.evidence_count for step in state.history]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts[-1], len(state.evidence))
        self.assertEqual(state.current_confidence, result.confidence)
        self.assertTrue(result.alternative_hypotheses)
        self.assertTrue(result.missing_information)
        changes = [h.confidence for step in state.history for h in step.assessment.hypotheses
                   if h.hypothesis_id == 'deployment-regression']
        self.assertLess(changes[0], changes[-1])
        self.assertEqual(InvestigationResult.model_validate_json(result.model_dump_json()), result)

    def test_tools_are_invoked_with_incident_arguments_and_dependency_branch(self):
        tools = ToolLayer(self.source)
        with patch.object(tools, 'get_metrics', wraps=tools.get_metrics) as metrics:
            run = Investigator(OfflineReferenceModel(), tools).run(self.incident)
        calls = run.state.tool_results
        self.assertEqual({r.call.name for r in calls}, {
            'search_logs', 'get_metrics', 'get_dependencies', 'get_recent_deployments', 'search_runbooks'})
        self.assertEqual(metrics.call_count, 4)
        self.assertEqual(calls[0].call.arguments['service'], self.incident.alert.service_id)
        self.assertEqual(calls[0].call.arguments['start_time'], self.incident.observation_start)
        database = [r for r in calls if r.call.arguments.get('service') == 'database']
        self.assertEqual([r.call.name for r in database], ['get_metrics', 'search_logs'])
        self.assertEqual(database[0].records, [])
        self.assertEqual(database[0].evidence_ids, [])

    def test_provenance_exactly_matches_tool_records(self):
        state = self.run_agent().state
        for result in state.tool_results:
            for ref, record in zip(result.evidence_ids, result.records, strict=True):
                evidence = state.evidence[ref]
                self.assertEqual(evidence.record, record)
                self.assertEqual(evidence.tool, result.call.name)
                self.assertIn(result.call_id, evidence.call_ids)
                self.assertTrue(evidence.source_id)
        for transition in state.history:
            available = {ref for r in state.tool_results[:transition.iteration] for ref in r.evidence_ids}
            for hypothesis in transition.assessment.hypotheses:
                self.assertLessEqual(set(hypothesis.supporting_evidence + hypothesis.contradicting_evidence), available)

    def test_iteration_limit_returns_valid_incomplete_triage(self):
        run = self.run_agent(limit=1)
        self.assertEqual(run.state.stop_reason, 'iteration_limit')
        self.assertEqual(run.state.iteration_count, 1)
        self.assertNotEqual(run.result.primary_hypothesis.hypothesis_id, 'deployment-regression')
        self.assertTrue(run.result.missing_information)
        self.assertLess(run.result.confidence, .8)

    def test_timing_alone_does_not_establish_deployment_cause(self):
        self.source.data['logs'] = [r for r in self.source.data['logs'] if r.level != 'ERROR']
        run = self.run_agent()
        self.assertEqual(run.state.stop_reason, 'iteration_limit')
        self.assertNotEqual(run.result.primary_hypothesis.hypothesis_id, 'deployment-regression')
        regression = next(h for h in run.state.assessment.hypotheses if h.hypothesis_id == 'deployment-regression')
        self.assertLess(regression.confidence, .5)
        original_sequence = ['get_metrics', 'search_logs', 'get_recent_deployments', 'get_dependencies']
        self.assertNotEqual([r.call.name for r in run.state.tool_results[:4]], original_sequence)

    def test_new_contradicting_dependency_evidence_revises_hypothesis(self):
        self.source.data['metrics'] = [r.model_copy(update={'value': .4})
            if r.service == 'payment-service' and r.metric_name == 'error_5xx_rate' else r
            for r in self.source.data['metrics']]
        run = self.run_agent()
        revisions = [h for step in run.state.history for h in step.assessment.hypotheses
                     if h.hypothesis_id == 'deployment-regression']
        self.assertGreater(revisions[0].confidence, revisions[-1].confidence)
        self.assertTrue(revisions[-1].contradicting_evidence)
        self.assertEqual(run.result.primary_hypothesis.hypothesis_id, 'dependency-failure')
        self.assertEqual(run.state.stop_reason, 'iteration_limit')

    def test_missing_dependency_data_is_not_healthy(self):
        self.source.data['logs'] = [r for r in self.source.data['logs'] if r.service != 'database']
        run = self.run_agent()
        self.assertEqual(run.state.stop_reason, 'iteration_limit')
        self.assertTrue(any('database' in gap for gap in run.result.missing_information))
        self.assertLess(run.result.confidence, .8)

    def test_no_evidence_yields_unknown_result(self):
        self.source.data.update(logs=[], metrics=[], deployments=[])
        run = self.run_agent()
        self.assertIsNone(run.result.primary_hypothesis)
        self.assertEqual(run.result.confidence, 0)
        self.assertEqual(run.result.affected_services, [])
        self.assertEqual(run.result.severity, 'UNKNOWN')

    def test_repeated_calls_deduplicate_evidence_and_preserve_measurements(self):
        class Repeating(OfflineReferenceModel):
            def select_tool(self, state):
                return ToolCall(name='get_metrics', reason='Repeated measurement', arguments={
                    'service': state.incident.alert.service_id,
                    'start_time': state.incident.observation_start,
                    'end_time': state.incident.observation_end})
        run = self.run_agent(Repeating(), limit=2)
        self.assertEqual(len(run.state.evidence), 60)
        self.assertTrue(all(e.call_ids == ['call-1', 'call-2'] for e in run.state.evidence.values()))
        self.assertEqual(len({e.source_id for e in run.state.evidence.values()}), 15)

    def test_no_file_reads_with_in_memory_tools(self):
        with patch('builtins.open', side_effect=AssertionError('File access forbidden')), \
             patch('io.open', side_effect=AssertionError('File access forbidden')):
            self.assertEqual(self.run_agent().state.stop_reason, 'sufficient_evidence')

    def test_real_source_reads_only_through_tool_layer_never_evaluation(self):
        real_io_open, real_open = io.open, builtins.open
        paths = []

        def checked(opener):
            def read(file, *args, **kwargs):
                if isinstance(file, (str, Path)):
                    path = Path(file).resolve()
                    self.assertNotIn('evaluation', path.parts)
                    paths.append(path)
                    frames = inspect.stack()
                    self.assertTrue(any(frame.function == '_records' and frame.filename.endswith('tools/source.py')
                                        for frame in frames), str(path))
                return opener(file, *args, **kwargs)
            return read
        with patch('io.open', checked(real_io_open)), patch('builtins.open', checked(real_open)):
            run = Investigator(OfflineReferenceModel()).run(self.incident)
        self.assertTrue(paths)
        self.assertEqual(run.result.primary_hypothesis.hypothesis_id, 'deployment-regression')

    def test_invalid_output_and_unknown_citations_rejected(self):
        result = self.run_agent().result.model_dump()
        for update in ({'confidence': 1.1}, {'severity': ''}, {'evidence_ids': ['invented']},
                       {'root_cause': 'invented'}, {'extra': True}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                InvestigationResult.model_validate({**result, **update})

        class Inventing(OfflineReferenceModel):
            def assess(self, state):
                return Assessment(hypotheses=[Hypothesis(hypothesis_id='bad', description='Invented',
                    confidence=.9, supporting_evidence=['invented'])], rationale='Invalid citation')
        with self.assertRaisesRegex(ValueError, 'no tool returned'):
            self.run_agent(Inventing())

    def test_model_cannot_mutate_runtime_state_or_rewrite_final_evidence(self):
        class Mutating(OfflineReferenceModel):
            def select_tool(self, state):
                decision = super().select_tool(state)
                state.evidence.clear()
                state.incident.incident_id = 'changed'
                return decision
        self.assertEqual(self.run_agent(Mutating()).result.incident_id, self.incident.incident_id)

        class Rewriting(OfflineReferenceModel):
            def finalize(self, state):
                result = super().finalize(state)
                result.supporting_evidence[0].record['value'] = .99
                return result
        with self.assertRaisesRegex(ValueError, 'changed tool evidence'):
            self.run_agent(Rewriting())

    def test_tool_selection_is_replaceable_and_arguments_validated(self):
        class LogsFirst(OfflineReferenceModel):
            def select_tool(self, state):
                if not state.tool_results:
                    return ToolCall(name='search_logs', reason='Inspect symptoms first', arguments={
                        'service': state.incident.alert.service_id,
                        'start_time': state.incident.observation_start,
                        'end_time': state.incident.observation_end})
                return super().select_tool(state)
        run = self.run_agent(LogsFirst())
        self.assertEqual(run.state.tool_results[0].call.name, 'search_logs')
        self.assertEqual(run.result.primary_hypothesis.hypothesis_id, 'deployment-regression')

        class BadArguments(OfflineReferenceModel):
            def select_tool(self, state):
                return ToolCall(name='get_metrics', arguments={'service': 'x'}, reason='Invalid')
        with self.assertRaises(ValidationError):
            self.run_agent(BadArguments())

    def test_ollama_transport_contract_mocked_only(self):
        model = OllamaModel('test-model')
        state = self.run_agent(limit=1).state
        outputs = [OfflineReferenceModel().assess(state), OfflineReferenceModel().select_tool(state),
                   OfflineReferenceModel().finalize(state)]
        for method, expected in zip((model.assess, model.select_tool, model.finalize), outputs):
            body = json.dumps({'message': {'content': expected.model_dump_json()}}).encode()
            with patch('signal_trace.agent.provider.urlopen', return_value=io.BytesIO(body)) as send:
                self.assertEqual(method(state), expected)
                payload = json.loads(send.call_args.args[0].data)
                self.assertFalse(payload['stream'])
                self.assertEqual(payload['model'], 'test-model')
                self.assertIn('properties', payload['format'])
                self.assertIn('get_metrics', payload['messages'][1]['content'])
        with self.assertRaises(ValueError):
            OllamaModel('')
        with patch('signal_trace.agent.provider.urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                model.assess(state)

    def test_configuration_and_iteration_validation(self):
        self.assertIsInstance(configured_investigator(Settings()).model, OfflineReferenceModel)
        self.assertIsInstance(configured_investigator(Settings(agent_provider='ollama', agent_model='test')).model, OllamaModel)
        for limit in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                Investigator(OfflineReferenceModel(), max_iterations=limit)

    def test_triage_endpoint_offline(self):
        with TestClient(create_app(Settings())) as client:
            response = client.post('/incidents/triage', json=self.incident.model_dump())
        self.assertEqual(response.status_code, 200)
        result = InvestigationResult.model_validate(response.json())
        self.assertEqual(result.primary_hypothesis.hypothesis_id, 'deployment-regression')


    def test_info_messages_alone_do_not_establish_dependency_health(self):
        self.source.data['logs'] = [r.model_copy(update={'message': 'Background maintenance started'})
            if r.service == 'database' else r for r in self.source.data['logs']]
        run = self.run_agent()
        self.assertEqual(run.state.stop_reason, 'iteration_limit')
        self.assertTrue(any('database' in gap for gap in run.result.missing_information))

    def test_preexisting_errors_contradict_deployment_regression(self):
        first = self.source.data['logs'][0]
        self.source.data['logs'][0] = first.model_copy(update={
            'level': 'ERROR', 'message': 'Application failed before change'})
        run = self.run_agent()
        regression = next(h for h in run.state.assessment.hypotheses if h.hypothesis_id == 'deployment-regression')
        self.assertLess(regression.confidence, .5)
        self.assertTrue(regression.contradicting_evidence)
        self.assertNotEqual(run.result.primary_hypothesis.hypothesis_id, 'deployment-regression')

    def test_service_names_and_versions_are_derived_from_tools(self):
        old = self.incident.alert.service_id
        renamed = 'renamed-service'
        self.incident.alert.service_id = renamed
        for name in ('logs', 'metrics', 'deployments'):
            self.source.data[name] = [r.model_copy(update={'service': renamed})
                if r.service == old else r for r in self.source.data[name]]
        self.source.data['deployments'] = [r.model_copy(update={'version': '9.8.7'})
                                         for r in self.source.data['deployments']]
        self.source.data['services'].remove(old)
        self.source.data['services'].add(renamed)
        self.source.data['edges'] = [(renamed if a == old else a, b) for a, b in self.source.data['edges']]
        run = self.run_agent()
        self.assertEqual(run.result.affected_services, [renamed])
        self.assertIn('version 9.8.7', run.result.root_cause)
        self.assertEqual(run.result.primary_hypothesis.hypothesis_id, 'deployment-regression')

    def test_runbook_chunks_keep_distinct_provenance(self):
        from signal_trace.retrieval.models import SearchResult
        book = self.source.data['runbooks'][0].model_dump(mode='json')
        rows = [SearchResult(chunk_id=f'chunk-{i}', document_id=book['runbook_id'],
            source=book['source'], services=book['services'], text=step,
            metadata={'runbook': book}, score=.7)
            for i, step in enumerate(book['steps'][:2])]
        class Retriever:
            def search(self, query, service, top_k):
                return rows
        run = Investigator(OfflineReferenceModel(), ToolLayer(self.source, Retriever())).run(self.incident)
        evidence = [e for e in run.state.evidence.values() if e.tool == 'search_runbooks']
        self.assertEqual({e.source_id for e in evidence}, {'chunk-0', 'chunk-1'})
        self.assertEqual(len({e.evidence_id for e in evidence}), 2)
        self.assertTrue(all(e.record['runbook_id'] == book['runbook_id'] for e in evidence))
        self.assertTrue(all(e in run.result.supporting_evidence for e in evidence))
