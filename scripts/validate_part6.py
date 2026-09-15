"""Validation-only fault injection; does not modify Agent implementation."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from test_agent import MemorySource
from test_agent_semantic import semantic_smoke
from signal_trace.agent import Investigator, OfflineReferenceModel
from signal_trace.agent.models import InvestigationResult
from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer

incident = IncidentRequest.model_validate_json((ROOT / 'scenarios/uc1_deployment_5xx/incident.json').read_text())
runs = {}

def capture(name, tools):
    run = Investigator(OfflineReferenceModel(), tools).run(incident)
    InvestigationResult.model_validate_json(run.result.model_dump_json())
    for step in run.state.history:
        available = {ref for result in run.state.tool_results[:step.iteration] for ref in result.evidence_ids}
        for h in step.assessment.hypotheses:
            assert set(h.supporting_evidence + h.contradicting_evidence) <= available
    for result in run.state.tool_results:
        for ref, record in zip(result.evidence_ids, result.records, strict=True):
            assert run.state.evidence[ref].record == record
    assert run.result.supporting_evidence == list(run.state.evidence.values())
    runs[name] = run
    return run

normal = capture('normal', ToolLayer())
tools = ToolLayer()
with patch.object(tools, 'get_recent_deployments', side_effect=TimeoutError('Injected deployment timeout')) as call:
    failed = capture('A_tool_timeout', tools)
    assert call.call_count == 2
assert not any(e.tool == 'get_recent_deployments' for e in failed.state.evidence.values())
assert failed.result.outcome == 'insufficient_evidence'
assert failed.result.confidence < normal.result.confidence
assert 'version' not in failed.result.root_cause

tools = ToolLayer()
original = tools.get_metrics
attempts = []
def flaky(**kwargs):
    attempts.append(kwargs)
    if len(attempts) == 1:
        raise TimeoutError('Injected transient timeout')
    return original(**kwargs)
with patch.object(tools, 'get_metrics', side_effect=flaky):
    recovered = capture('A_recovered', tools)
assert recovered.state.tool_results[0].attempts == 2
assert recovered.result.confidence == normal.result.confidence

source = MemorySource()
source.data['metrics'] = [r.model_copy(update={'value': .4})
    if r.service == 'payment-service' and r.metric_name == 'error_5xx_rate' else r
    for r in source.data['metrics']]
conflict = capture('B_conflict', ToolLayer(source))
revisions = [h for step in conflict.state.history for h in step.assessment.hypotheses
             if h.hypothesis_id == 'deployment-regression']
assert revisions[0].confidence > revisions[-1].confidence
assert revisions[-1].contradicting_evidence
assert conflict.result.outcome == 'insufficient_evidence'
assert conflict.result.confidence <= .6

tools = ToolLayer()
original = tools.get_metrics
def no_primary_metrics(**kwargs):
    return [] if kwargs['service'] == incident.alert.service_id else original(**kwargs)
with patch.object(tools, 'get_metrics', side_effect=no_primary_metrics):
    insufficient = capture('C_missing_metrics', tools)
assert insufficient.result.root_cause is None
assert insufficient.result.confidence == 0
assert insufficient.result.missing_information
assert insufficient.result.outcome == 'insufficient_evidence'

# Existing Part 5 artifact is only read after all investigations finish.
baseline = json.loads((ROOT / 'reports/uc1_agent_run.json').read_text())
comparison = {}
for field in ('root_cause', 'confidence', 'severity', 'affected_services', 'primary_hypothesis', 'alternative_hypotheses', 'recommended_actions'):
    comparison[field] = normal.result.model_dump(mode='json')[field] == baseline['result'][field]
assert all(comparison.values()), comparison
comparison['tool_calls'] = [r.call.model_dump(mode='json') for r in normal.state.tool_results] == [r['call'] for r in baseline['state']['tool_results']]
comparison['evidence'] = normal.state.model_dump(mode='json')['evidence'] == baseline['state']['evidence']
assert comparison['tool_calls'] and comparison['evidence']

semantic, audit = semantic_smoke('postgresql:///signal_trace_part5_test?host=/tmp/signal-trace-pg-socket&port=55432')
runs['normal_semantic'] = semantic
output = {'part5_comparison': comparison, 'semantic_audit': audit,
          'runs': {name: run.model_dump(mode='json') for name, run in runs.items()}}
(ROOT / 'reports/part6_scenarios.json').write_text(json.dumps(output, indent=2) + '\n')
for name, run in runs.items():
    print(name, run.state.iteration_count, len(run.state.evidence), run.result.confidence, run.result.outcome, run.state.stop_reason)
print('Part 5 comparison:', comparison)
