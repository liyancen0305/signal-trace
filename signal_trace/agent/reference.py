"""Deterministic evidence-driven reference policy, explicitly NOT a live LLM.

Contains no fixture IDs, service names, versions, or incident timestamps.
"""
from datetime import datetime, timedelta
import re

from signal_trace.agent.models import Assessment, Hypothesis, InvestigationResult, ToolCall


def observations(state, tool, service=None):
    return [e for e in state.evidence.values() if e.tool == tool
            and (service is None or e.record.get('service') == service)]


def called(state, tool, service=None):
    return any(r.call.name == tool and (service is None or r.call.arguments.get('service') == service)
               for r in state.tool_results)


def rates(state, service):
    return sorted([e for e in observations(state, 'get_metrics', service)
                   if e.record['metric_name'] == 'error_5xx_rate'],
                  key=lambda e: e.record['timestamp'])


def refs(rows):
    return list(dict.fromkeys(e.evidence_id for e in rows))


def facts(state):
    service = state.incident.alert.service_id
    threshold = state.incident.alert.threshold
    series = rates(state, service)
    high = [e for e in series if e.record['value'] > threshold]
    errors = [e for e in observations(state, 'search_logs', service)
              if e.record['level'] in ('ERROR', 'CRITICAL')]
    onset = high[0].record['timestamp'] if high else None
    deployments = [e for e in observations(state, 'get_recent_deployments', service)
                   if onset and state.incident.observation_start <= e.record['timestamp'] <= onset
                   and e.record.get('metadata') and e.record['metadata']['status'] == 'succeeded']
    deployments.sort(key=lambda e: e.record['timestamp'], reverse=True)
    change = deployments[0] if deployments else None
    baseline = [e for e in series if change and e.record['timestamp'] < change.record['timestamp']
                and e.record['value'] <= threshold]
    before_errors = [e for e in errors if change and e.record['timestamp'] < change.record['timestamp']]
    after_errors = [e for e in errors if change and e.record['timestamp'] >= change.record['timestamp']]
    topology = observations(state, 'get_dependencies', service)
    downstream = topology[-1].record['downstream'] if topology else []
    healthy, unhealthy, unknown = [], [], []
    for dependency in downstream:
        samples = rates(state, dependency)
        logs = observations(state, 'search_logs', dependency)
        bad = [e for e in samples if e.record['value'] > threshold]
        bad += [e for e in logs if e.record['level'] in ('ERROR', 'CRITICAL')]
        if bad:
            unhealthy.extend(bad[:1])
        elif samples and high and {e.record['timestamp'] for e in high} <= {e.record['timestamp'] for e in samples}:
            healthy.extend(samples[-1:])
        elif logs and not samples and high:
            health_logs = [e for e in logs if e.record['level'] == 'INFO'
                           and re.search(r'\bhealthy\b', str(e.record['message']).lower())
                           and 'not healthy' not in str(e.record['message']).lower()]
            covered = all(any(
                datetime.fromisoformat(str(window.record['timestamp']).replace('Z', '+00:00')) <= datetime.fromisoformat(str(log.record['timestamp']).replace('Z', '+00:00'))
                < datetime.fromisoformat(str(window.record['timestamp']).replace('Z', '+00:00')) + timedelta(seconds=window.record['window_seconds'])
                for log in health_logs) for window in high)
            if covered:
                healthy.extend(health_logs[-1:])
            else:
                unknown.append(dependency)
        else:
            unknown.append(dependency)
    return service, series, high, errors, change, baseline, before_errors, after_errors, topology, downstream, healthy, unhealthy, unknown


class OfflineReferenceModel:
    name = 'offline-reference-v1 (deterministic; no LLM)'

    def assess(self, state):
        (service, series, high, errors, change, baseline, before_errors, after_errors,
         topology, downstream, healthy, unhealthy, unknown) = facts(state)
        hypotheses, missing = [], []
        if not series:
            missing.append('Service error-rate measurements')
        if not called(state, 'search_logs', service):
            missing.append('Application error signatures')
        if not called(state, 'get_recent_deployments', service):
            missing.append('Recent change history')
        if not topology or not topology[-1].record['service_known']:
            missing.append('Known dependency topology')
        if unknown:
            missing.append('Dependency health: ' + ', '.join(unknown))
        if not observations(state, 'search_runbooks'):
            missing.append('Relevant investigation guidance')
        if high:
            impact = [high[0], high[-1]]
            hypotheses.append(Hypothesis(hypothesis_id='local-failure',
                description=f'Local application failure in {service}; trigger not established.',
                confidence=0.55 if errors else 0.3,
                supporting_evidence=refs(impact + errors[:1]), contradicting_evidence=refs(unhealthy)))
            hypotheses.append(Hypothesis(hypothesis_id='dependency-failure',
                description='A downstream failure may be propagating to the alerted service.',
                confidence=0.7 if unhealthy else (0.15 if healthy and not unknown else 0.35),
                supporting_evidence=refs(impact + unhealthy), contradicting_evidence=refs(healthy)))
        if change and high:
            corroborated = bool(baseline and after_errors and not before_errors)
            confidence = 0.88 if corroborated and healthy and not unknown and not unhealthy else 0.55 if corroborated else 0.25
            if before_errors or unhealthy:
                confidence = 0.2
            detail = f" Observed error: {after_errors[0].record['message']}" if after_errors else ''
            hypotheses.append(Hypothesis(hypothesis_id='deployment-regression',
                description=f"Likely deployment regression in {service} after version {change.record['version']}.{detail}",
                confidence=confidence, supporting_evidence=refs([change] + baseline[:1] + high[:1] + after_errors[:1] + healthy),
                contradicting_evidence=refs(before_errors[:1] + unhealthy)))
            if not corroborated:
                missing.append('Independent evidence connecting the change to new application failures')
        if high and not change:
            missing.append('A causal trigger for the application failures')
        if not high:
            missing.append('Observed elevated error rate within the supplied interval')
        if errors:
            missing.append('Code/configuration diff and reproduction to confirm the failure mechanism')
        primary = max(hypotheses, key=lambda h: h.confidence) if hypotheses else None
        sufficient = bool(primary and primary.confidence >= 0.8 and topology
                          and not unknown and called(state, 'search_runbooks'))
        return Assessment(hypotheses=hypotheses,
            primary_hypothesis_id=primary.hypothesis_id if primary else None,
            missing_information=missing, sufficient_evidence=sufficient,
            rationale='Reassessed change timing, baseline/incident error rates, application errors, and sampled dependency health.')

    def select_tool(self, state):
        (service, series, high, errors, change, baseline, before_errors, after_errors,
         topology, downstream, healthy, unhealthy, unknown) = facts(state)
        interval = {'start_time': state.incident.observation_start, 'end_time': state.incident.observation_end}

        def call(name, reason, target=service, **arguments):
            return ToolCall(name=name, arguments={'service': target, **arguments}, reason=reason)

        if not called(state, 'get_metrics', service):
            return call('get_metrics', 'Measure impact and baseline', **interval, metric_name='error_5xx_rate')
        if high and not called(state, 'search_logs', service):
            return call('search_logs', 'Inspect error signatures across baseline and incident', **interval)
        if errors and not called(state, 'get_recent_deployments', service):
            return call('get_recent_deployments', 'Check changes as a possible trigger, not proof of cause', since=state.incident.observation_start)
        if not topology:
            return call('get_dependencies', 'Identify competing downstream explanations')
        for dependency in downstream:
            if not called(state, 'get_metrics', dependency):
                return call('get_metrics', 'Compare downstream error rates', target=dependency,
                            **interval, metric_name='error_5xx_rate')
            if not rates(state, dependency) and not called(state, 'search_logs', dependency):
                return call('search_logs', 'No HTTP metric series; inspect dependency health logs', target=dependency, **interval)
        if not called(state, 'search_runbooks'):
            return call('search_runbooks', 'Retrieve investigation and mitigation guidance', query='5xx', top_k=5)
        if not called(state, 'search_logs', service):
            return call('search_logs', 'Look for an explanation when metrics are inconclusive', **interval)
        if not called(state, 'get_recent_deployments', service):
            return call('get_recent_deployments', 'Check unresolved change history', since=state.incident.observation_start)
        # Repeat unresolved checks; the iteration limit bounds static investigations.
        return call('get_metrics', 'Recheck unresolved impact before the iteration budget ends', **interval)

    def finalize(self, state):
        assessment = state.assessment
        primary = next((h for h in assessment.hypotheses if h.hypothesis_id == assessment.primary_hypothesis_id), None)
        cited = list(dict.fromkeys(r for h in assessment.hypotheses
                                  for r in h.supporting_evidence + h.contradicting_evidence))
        series = rates(state, state.incident.alert.service_id)
        consecutive = longest = 0
        previous_end = None
        for e in series:
            start = datetime.fromisoformat(str(e.record['timestamp']).replace('Z', '+00:00'))
            end = start + timedelta(seconds=e.record['window_seconds'])
            complete = end <= datetime.fromisoformat(state.incident.observation_end.replace('Z', '+00:00'))
            if complete and e.record['value'] > state.incident.alert.threshold:
                consecutive = consecutive + 1 if previous_end == start else 1
            else:
                consecutive = 0
            longest = max(longest, consecutive)
            previous_end = end
        elevated = longest >= state.incident.alert.consecutive_windows
        cited = list(dict.fromkeys(cited + refs(series) + refs(observations(state, 'search_runbooks'))))
        actions = ['Inspect code/configuration changes and reproduce the observed error.',
                   'Verify error-rate recovery across several complete metric windows after any approved mitigation.']
        if primary and primary.hypothesis_id == 'deployment-regression':
            actions.insert(0, 'Propose rollback to the last verified healthy version through the normal human approval process.')
        else:
            actions.insert(0, 'Collect the missing evidence before choosing a mitigation.')
        return InvestigationResult(incident_id=state.incident.incident_id,
            summary=primary.description if primary else 'Insufficient operational evidence to identify a likely cause.',
            root_cause=primary.description if primary else None,
            affected_service=state.incident.alert.service_id if elevated else None,
            affected_services=[state.incident.alert.service_id] if elevated else [],
            severity='SEV-2' if elevated else 'UNKNOWN',
            severity_rationale='Provisional local policy: sustained 5xx above the alert threshold is SEV-2; not an organization-wide severity classification.' if elevated else 'Insufficient measured impact for the provisional severity policy.',
            evidence_ids=cited, primary_hypothesis=primary,
            confidence=primary.confidence if primary else 0.0,
            supporting_evidence=[state.evidence[r] for r in cited],
            alternative_hypotheses=[h for h in assessment.hypotheses if h != primary],
            missing_information=assessment.missing_information, recommended_actions=actions)
