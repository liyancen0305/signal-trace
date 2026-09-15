"""Post-run Part 5 artifact audit; never feeds evaluation labels to an Agent.

Run after the semantic smoke, full unittest suite, and retrieval evaluation.
Requires reports/uc1_agent_semantic_{run,audit}.json, part5_regression.log,
part5_retrieval_evaluation.json, and the existing Part 4 reference report.
Writes reports/part5_grounding_audit.json; assertion failures propagate.
"""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
from signal_trace.agent.models import InvestigationRun, InvestigationResult
from signal_trace.tools.source import SyntheticSource

root = Path(__file__).resolve().parents[1]
reports = root / 'reports'
run = InvestigationRun.model_validate_json((reports / 'uc1_agent_semantic_run.json').read_text())
state, result = run.state, run.result
semantic = json.loads((reports / 'uc1_agent_semantic_audit.json').read_text())
Draft202012Validator(InvestigationResult.model_json_schema()).validate(result.model_dump(mode='json'))

# This is a post-run evaluator. It never calls an Agent or supplies it with labels.
source = SyntheticSource()
originals = {tool: [row.model_dump(mode='json') for row in getattr(source, name)()]
             for tool, name in [('get_metrics', 'metrics'), ('search_logs', 'logs'),
                                ('get_recent_deployments', 'deployments')]}
seen, provenance_checks, history_checks = set(), 0, 0
for call in state.tool_results:
    assert len(call.records) == len(call.evidence_ids)
    for record, ref in zip(call.records, call.evidence_ids, strict=True):
        evidence = state.evidence[ref]
        digest = hashlib.sha256(json.dumps([call.call.name, record], sort_keys=True).encode()).hexdigest()[:20]
        assert ref == 'ev-' + digest
        assert evidence.record == record and evidence.tool == call.call.name
        assert call.call_id in evidence.call_ids
        if call.call.name in originals:
            assert record in originals[call.call.name]
        elif call.call.name == 'get_dependencies':
            assert record['downstream'] == sorted(b for a, b in source.edges() if a == record['service'])
            assert record['upstream'] == sorted(a for a, b in source.edges() if b == record['service'])
        elif call.call.name == 'search_runbooks':
            row = next(row for search in semantic['semantic_calls'] for row in search['results']
                       if row['chunk_id'] == evidence.source_id)
            for key in ('text', 'score', 'source', 'metadata'):
                assert record[key] == row[key]
        seen.add(ref)
        provenance_checks += 1
assert seen == set(state.evidence)
for transition in state.history:
    available = {ref for call in state.tool_results[:transition.iteration] for ref in call.evidence_ids}
    assert transition.evidence_count == len(available)
    for hypothesis in transition.assessment.hypotheses:
        refs = hypothesis.supporting_evidence + hypothesis.contradicting_evidence
        assert set(refs) <= available
        history_checks += len(refs)
for evidence in result.supporting_evidence:
    assert evidence == state.evidence[evidence.evidence_id]

primary = result.primary_hypothesis
support = [state.evidence[ref] for ref in primary.supporting_evidence]
change = next(e for e in support if e.tool == 'get_recent_deployments')
series = sorted([e for e in state.evidence.values() if e.tool == 'get_metrics'
                 and e.record['service'] == result.affected_service], key=lambda e: e.record['timestamp'])
baseline = [e for e in series if e.record['timestamp'] < change.record['timestamp']]
high = [e for e in series if e.record['value'] > state.incident.alert.threshold]
errors = [e for e in state.evidence.values() if e.tool == 'search_logs'
          and e.record['service'] == result.affected_service and e.record['level'] == 'ERROR']
assert baseline and high and errors
assert all(e.record['timestamp'] >= change.record['timestamp'] for e in errors)
assert errors[0].record['message'] in primary.description
assert change.record['version'] in primary.description
assert {'get_metrics', 'search_logs', 'get_recent_deployments'} <= {e.tool for e in support}
longest = consecutive = 0
previous_end = None
for e in series:
    start = datetime.fromisoformat(e.record['timestamp'].replace('Z', '+00:00'))
    end = start + timedelta(seconds=e.record['window_seconds'])
    if end <= datetime.fromisoformat(state.incident.observation_end.replace('Z', '+00:00')) and e.record['value'] > state.incident.alert.threshold:
        consecutive = consecutive + 1 if previous_end == start else 1
    else:
        consecutive = 0
    longest = max(longest, consecutive)
    previous_end = end
assert longest >= state.incident.alert.consecutive_windows
assert semantic['keyword_fallback_used'] is False
assert semantic['raw_runbook_and_evaluation_reads_during_investigation'] is False

# Load expected outcomes only after the completed run has been audited.
truth = json.loads((root / 'evaluation/uc1_deployment_5xx.json').read_text())
comparison = {
    'affected_service': {'expected': truth['affected_service'], 'actual': result.affected_service,
                         'matches': result.affected_service == truth['affected_service']},
    'root_cause': {'expected': truth['root_cause'], 'actual': result.root_cause,
                   'matches': 'deployment regression' in result.root_cause.lower()
                   and truth['affected_service'] in result.root_cause},
    'deployment': {'expected_id': truth['deployment_id'], 'actual_id': change.source_id,
                   'expected_version': '2.3.1', 'actual_version': change.record['version'],
                   'matches': change.source_id == truth['deployment_id'] and change.record['version'] == '2.3.1'},
    'severity': {'expected': truth['severity'], 'actual': result.severity,
                 'matches': result.severity == truth['severity']},
}
assert all(row['matches'] for row in comparison.values())
log = (reports / 'part5_regression.log').read_text()
tests = re.findall(r'^test_\S+ \(([^.]+)\..+\) \.\.\. ok$', log, re.M)
summary = re.search(r'Ran (\d+) tests in ', log)
assert summary and len(tests) == int(summary[1]) and '\nOK\n' in log and 'skipped' not in log
retrieval = json.loads((reports / 'part5_retrieval_evaluation.json').read_text())
prior = json.loads((reports / 'retrieval_final.json').read_text())
metrics = ['recall_at_1', 'recall_at_3', 'mrr_at_3', 'negative_queries_with_results']
assert all(retrieval[k] == prior[k] for k in metrics)
claims = {
    'deployment_and_version': [change.evidence_id],
    'baseline_and_elevated_5xx': [baseline[0].evidence_id, high[0].evidence_id],
    'new_local_error_signature': [errors[0].evidence_id],
    'sampled_dependency_health': [e.evidence_id for e in support if e.record.get('service') != result.affected_service],
    'provisional_severity': [e.evidence_id for e in high],
    'investigation_and_human_approved_mitigation_guidance': [e.evidence_id for e in state.evidence.values() if e.tool == 'search_runbooks'],
}
final_refs = {e.evidence_id for e in result.supporting_evidence}
assert all(set(refs) <= final_refs for refs in claims.values())
audit = {
    'checked_at': datetime.now(timezone.utc).isoformat(),
    'run_model': run.model, 'scope': 'Completed semantic run with offline reference provider; no live LLM claims',
    'schema_valid': True, 'evidence_records_verified': provenance_checks,
    'historical_citation_references_verified': history_checks,
    'final_evidence_records_verified': len(result.supporting_evidence),
    'tool_records_match_operational_source': True, 'all_citations_exist_at_time_of_use': True,
    'semantic_provenance_verified': True, 'ground_truth_read_only_after_investigation': True,
    'deployment_corroborated_by_metrics_and_local_errors': True,
    'complete_consecutive_elevated_windows': longest, 'claim_evidence_map': claims,
    'ground_truth_comparison': comparison,
    'regression': {'passed': len(tests), 'failed': 0, 'skipped': 0, 'by_module': dict(sorted(Counter(tests).items()))},
    'retrieval_evaluation': {k: retrieval[k] for k in metrics},
    'retrieval_metrics_match_part4_report': True,
    'limits': ['Citation existence and payload equality do not prove arbitrary natural-language entailment.',
               'Causation is a likely triage inference; no code diff, reproduction, or rollback outcome was observed.',
               'Final dependency citations are representative samples; full interval observations are retained in AgentState.',
               'Confidence and SEV-2 are reference-policy outputs, not calibrated probability or an external severity standard.',
               'Live provider behavior remains unvalidated.'],
}
(reports / 'part5_grounding_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
print(json.dumps({k: audit[k] for k in ['schema_valid', 'evidence_records_verified', 'historical_citation_references_verified',
                                     'final_evidence_records_verified', 'complete_consecutive_elevated_windows', 'regression']}, indent=2))
