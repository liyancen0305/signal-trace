"""Versioned deterministic judgments, deliberately conservative about free text."""
import hashlib
import json
import re

from signal_trace.agent.models import InvestigationRun
from signal_trace.agent.workflow import TOOL_INPUTS

SCORER_VERSION = '1.0'
HIGH_CONFIDENCE = .8


def normalized(text):
    return ' '.join((text or '').casefold().split())


def call_key(call):
    args = TOOL_INPUTS[call['name']].model_validate_json(json.dumps(call['arguments']))
    return call['name'], json.dumps(args.model_dump(mode='json'), sort_keys=True)


def unsupported_claims(result, evidence, threshold=.05):
    """Recognize bounded claim templates; unknown prose fails closed for review.

    This is not a general natural-language entailment model. A claim outside this
    grammar is explicitly unverified, never silently counted as supported.
    """
    issues = []
    hypotheses = result.get('alternative_hypotheses', []) + ([result['primary_hypothesis']] if result.get('primary_hypothesis') else [])
    for h in hypotheses:
        rows = [evidence[r]['record'] for r in h['supporting_evidence'] if r in evidence]
        text = h['description']
        low = normalized(text)
        high = any(r.get('metric_name') == 'error_5xx_rate' and r.get('value', 0) > threshold for r in rows)
        services = {r.get('service') for r in rows}
        for service in re.findall(r'\b[\w-]+-service\b', text):
            if service not in services:
                issues.append(f'{h["hypothesis_id"]}: service {service} absent from cited records')
        for version in re.findall(r'\bversion\s+([\w.]+)', text, re.I):
            if not any(r.get('version') == version.rstrip('.') for r in rows):
                issues.append(f'{h["hypothesis_id"]}: unsupported version {version}')
        if re.fullmatch(r'local application failure in [\w-]+; trigger not established\.', low):
            supported = high
        elif low == 'a downstream failure may be propagating to the alerted service.':
            supported = high  # Explicit possibility, not an asserted causal mechanism.
        elif re.fullmatch(r'likely deployment regression in [\w-]+ after version [\w.]+\.(?: observed error: .+)?', low):
            changes = [r for r in rows if 'deployment_id' in r]
            errors = [r for r in rows if r.get('level') in ('ERROR', 'CRITICAL')]
            supported = high and bool(changes) and bool(errors)
            if ' Observed error: ' in text:
                quoted = text.split(' Observed error: ', 1)[1]
                supported = supported and any(r.get('message') == quoted for r in errors)
        elif re.fullmatch(r'[\w-]+-service memory exhaustion\.?', low):
            supported = any(r.get('metric_name') == 'memory_utilization' and r.get('value', 0) >= .95 for r in rows) and any(
                'outofmemory' in normalized(r.get('message')) or 'heap exhausted' in normalized(r.get('message')) for r in rows)
        elif re.fullmatch(r'[\w-]+-service (?:outage|failure) propagating to [\w-]+-service\.?', low):
            origin, target = re.findall(r'[\w-]+-service', low)
            supported = any(r.get('service') == origin and r.get('metric_name') == 'error_5xx_rate' and r.get('value', 0) > threshold for r in rows) and any(
                r.get('service') == target and origin.removesuffix('-service') in normalized(r.get('message')) for r in rows)
        else:
            supported = False
        if not supported:
            issues.append(f'{h["hypothesis_id"]}: unsupported or unverified claim: {text}')
    summary = result.get('summary', '')
    stripped = summary.removeprefix('Insufficient evidence for a confirmed cause. ')
    if stripped not in (result.get('root_cause'), 'Insufficient operational evidence to identify a likely cause.'):
        issues.append('Summary contains unsupported or unverified prose')
    known_actions = {
        'Inspect code/configuration changes and reproduce the observed error.',
        'Verify error-rate recovery across several complete metric windows after any approved mitigation.',
        'Propose rollback to the last verified healthy version through the normal human approval process.',
        'Collect the missing evidence before choosing a mitigation.',
        'Collect the recorded missing information before choosing a mitigation.',
        'Escalate to a human reviewer before choosing mitigation.',
    }
    for action in result.get('recommended_actions', []):
        if action not in known_actions:
            issues.append('Unverified recommendation: ' + action)
    for gap in result.get('missing_information', []):
        known = gap in {
            'Service error-rate measurements', 'Application error signatures', 'Recent change history',
            'Known dependency topology', 'Relevant investigation guidance',
            'Independent evidence connecting the change to new application failures',
            'A causal trigger for the application failures',
            'Observed elevated error rate within the supplied interval',
            'Code/configuration diff and reproduction to confirm the failure mechanism',
            'Resolve conflicting evidence for the primary hypothesis',
        }
        runtime_gap = re.fullmatch(r'(?:get_metrics|search_logs|get_recent_deployments|get_dependencies|search_runbooks) \{.*\}: (?:failed|empty|invalid)', gap)
        if not known and not runtime_gap and not gap.startswith((
            'Dependency health: ', 'Unknown dependency topology for ',
            'Repeated ', 'Investigation stopped: ', 'Model ')):
            issues.append('Unverified missing-information statement: ' + gap)
    observed_services = {e['record'].get('service') for e in evidence.values()
                         if e['record'].get('metric_name') == 'error_5xx_rate' and e['record'].get('value', 0) > threshold}
    if not set(result.get('affected_services', [])) <= observed_services:
        issues.append('Affected services lack cited measured impact')
    if result.get('severity_rationale') not in (
        'Provisional local policy: sustained 5xx above the alert threshold is SEV-2; not an organization-wide severity classification.',
        'Insufficient measured impact for the provisional severity policy.'):
        issues.append('Unverified severity rationale')
    return list(dict.fromkeys(issues))


def grounding(run, ledger):
    errors = []
    try:
        InvestigationRun.model_validate_json(json.dumps(run))
    except (ValueError, TypeError) as exc:
        errors.append('Invalid structured run: ' + str(exc))
    state, result = run['state'], run['result']
    evidence = state['evidence']
    calls = {r['call_id']: r for r in state['tool_results']}
    for ref, row in evidence.items():
        digest = hashlib.sha256(json.dumps([row['tool'], row['record']], sort_keys=True).encode()).hexdigest()[:20]
        expected_source = next((str(row['record'][k]) for k in ('log_id', 'metric_id', 'deployment_id', 'chunk_id', 'runbook_id')
                                if row['record'].get(k)), 'topology:' + str(row['record'].get('service')))
        if ref != 'ev-' + digest or row['evidence_id'] != ref or row['source_id'] != expected_source:
            errors.append('Invalid evidence identity/provenance: ' + ref)
        if not row['call_ids']:
            errors.append('Missing call provenance: ' + ref)
        for call_id in row['call_ids']:
            call = calls.get(call_id)
            if not call or ref not in call['evidence_ids'] or row['record'] not in call['records'] or row['tool'] != call['call']['name']:
                errors.append('Invalid call provenance: ' + ref)
            elif not any(entry['tool'] == row['tool'] and row['record'] in entry['records']
                         and call_key({'name': entry['tool'], 'arguments': entry['arguments']}) == call_key(call['call'])
                         for entry in ledger):
                errors.append('Evidence absent from independent tool ledger: ' + ref)
    for row in result.get('supporting_evidence', []):
        if evidence.get(row['evidence_id']) != row:
            errors.append('Final evidence changed: ' + row['evidence_id'])
    for step in state['history']:
        available = {ref for call in state['tool_results'] if int(call['call_id'].split('-')[-1]) <= step['iteration'] for ref in call['evidence_ids']}
        for h in step['assessment']['hypotheses']:
            if not set(h['supporting_evidence'] + h['contradicting_evidence']) <= available:
                errors.append('Hypothesis cited unavailable evidence at iteration ' + str(step['iteration']))
    claims = unsupported_claims(result, evidence, state['incident']['alert']['threshold'])
    return {'pass': not errors and not claims, 'reference_errors': errors, 'unsupported_claims': claims,
            'scope': 'Exact provenance plus bounded claim grammar; unrecognized prose fails closed.'}


def score_case(case_id, truth, execution):
    run, ledger = execution['run'], execution['ledger']
    state, result = run['state'], run['result']
    primary = result['primary_hypothesis']
    root_ok = bool(primary and any(re.search(pattern, normalized(primary['description'])) for pattern in truth['root_patterns']))
    if primary and re.search(r'\b(?:not|ruled out|unlikely)\b', normalized(primary['description']).replace('trigger not established', 'unknown trigger')):
        root_ok = False
    if primary is None and truth.get('allow_no_primary', False):
        root_ok = result['root_cause'] is None
    affected_ok = set(result['affected_services']) == set(truth['affected_services'])
    severity_ok = normalized(result['severity']) == normalized(truth['severity'])
    grounded = grounding(run, ledger)
    cited = set(primary['supporting_evidence']) if primary else set()
    cited_sources = {state['evidence'][ref]['source_id'] for ref in cited if ref in state['evidence']}
    missing_important = sorted(set(truth['important_evidence']) - cited_sources)
    calls = state['tool_results']
    missing_tools = [required for required in truth['required_tools'] if not any(
        c['call']['name'] == required['tool'] and ('service' not in required or c['call']['arguments'].get('service') == required['service'])
        for c in calls if c['status'] != 'skipped')]
    keys = [call_key(c['call']) for c in calls]
    repeats = len(keys) - len(set(keys))
    # Retries are separately counted, not mislabeled as repeated model decisions.
    retries = sum(max(0, c['attempts'] - 1) for c in calls)
    no_new = [c['call_id'] for i, c in enumerate(calls) if c['status'] == 'success' and c['evidence_ids']
              and all(any(ref in earlier['evidence_ids'] for earlier in calls[:i]) for ref in c['evidence_ids'])]
    excess = max(0, len(calls) - truth['max_calls'])
    tools = {'pass': not missing_tools and not repeats and not excess and not no_new,
             'missing_sources': missing_tools, 'repeated_calls': repeats, 'retry_attempts': retries,
             'unnecessary_calls_over_budget': excess, 'no_new_evidence_calls': no_new,
             'decision_count': len(calls), 'actual_tool_attempts': len(ledger)}
    early = bool(missing_tools) or state['stop_reason'] == 'sufficient_evidence' and (not root_ok or bool(missing_important) or truth['uncertainty'])
    continued = any(s['assessment']['sufficient_evidence'] and s['iteration'] < state['iteration_count'] for s in state['history']) or bool(excess)
    limit = state['stop_reason'] == 'iteration_limit'
    stopping = {'pass': not early and not continued and not limit,
                'too_early': early, 'unnecessary_continuation': continued, 'iteration_limit': limit,
                'reason': state['stop_reason']}
    confidence = result['confidence']
    critical_missing = bool(missing_important) or any(c['status'] in ('failed', 'invalid') for c in calls)
    conflict = bool(primary and primary['contradicting_evidence'])
    confidence_quality = {'pass': truth['confidence_min'] <= confidence <= truth['confidence_max']
                         and not (confidence >= HIGH_CONFIDENCE and (not root_ok or critical_missing))
                         and not (conflict and confidence > .6),
                         'high_confidence_wrong': confidence >= HIGH_CONFIDENCE and not root_ok,
                         'high_confidence_missing': confidence >= HIGH_CONFIDENCE and critical_missing,
                         'overconfident_conflict': conflict and confidence > .6,
                         'value': confidence, 'probability_interpretation': False}
    human_review = any(re.search(r'\b(human|review|escalat\w*)\b', a, re.I) for a in result['recommended_actions'])
    missing_reported = bool(result['missing_information']) and all(
        any(term in gap for gap in result['missing_information']) for term in truth.get('required_missing_terms', []))
    reliability = {'applicable': truth['reliability_case'], 'pass': None,
                   'missing_information_reported': missing_reported,
                   'human_review_recommended': human_review}
    if truth['reliability_case']:
        reliability['pass'] = missing_reported and confidence <= truth['confidence_max'] and grounded['pass'] \
            and result['outcome'] == 'insufficient_evidence' and (human_review or not truth['human_review_required'])
    scores = {'root_cause': root_ok, 'affected_services': affected_ok, 'severity': severity_ok,
              'important_evidence': not missing_important, 'outcome': result['outcome'] == truth['expected_outcome']}
    overall = all(scores.values()) and grounded['pass'] and tools['pass'] and stopping['pass'] and confidence_quality['pass'] and reliability['pass'] is not False
    return {'case_id': case_id, 'scenario': truth['scenario'], 'expected': truth,
            'actual': result, 'confidence': confidence, 'tool_sequence': [c['call'] for c in calls],
            'scores': scores, 'grounding': grounded, 'unsupported_claims': grounded['unsupported_claims'],
            'missing_important_evidence': missing_important, 'tool_usage': tools, 'stopping': stopping,
            'confidence_quality': confidence_quality, 'reliability': reliability, 'overall_pass': overall}


def aggregate(cases):
    if not cases:
        raise ValueError('Cannot aggregate an empty evaluation suite')
    count = len(cases)
    rate = lambda fn: sum(bool(fn(c)) for c in cases) / count
    failures = [c for c in cases if c['reliability']['applicable']]
    decisions = sum(c['tool_usage']['decision_count'] for c in cases)
    return {'cases': count, 'overall_pass_rate': rate(lambda c: c['overall_pass']),
            'root_cause_accuracy': rate(lambda c: c['scores']['root_cause']),
            'affected_service_accuracy': rate(lambda c: c['scores']['affected_services']),
            'severity_accuracy': rate(lambda c: c['scores']['severity']),
            'grounding_pass_rate': rate(lambda c: c['grounding']['pass']),
            'unsupported_claim_rate': rate(lambda c: bool(c['unsupported_claims'])),
            'unsupported_claim_rate_denominator': 'cases with at least one unsupported or unverified claim / all cases',
            'average_tool_calls': decisions / count,
            'repeated_call_rate': sum(c['tool_usage']['repeated_calls'] for c in cases) / decisions if decisions else 0,
            'repeated_call_rate_denominator': 'repeated normalized decisions / all tool decisions (includes suppressed calls; excludes retries)',
            'high_confidence_wrong_answer_count': sum(c['confidence_quality']['high_confidence_wrong'] for c in cases),
            'high_confidence_missing_evidence_count': sum(c['confidence_quality']['high_confidence_missing'] for c in cases),
            'failure_handling_pass_rate': sum(c['reliability']['pass'] is True for c in failures) / len(failures) if failures else None,
            'failure_handling_cases': len(failures)}
