"""Bounded, model-directed loop. All operational access goes through ToolLayer."""
import hashlib
import json
import math
import queue
import threading
from urllib.error import HTTPError, URLError

from pydantic import BaseModel

from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer
from signal_trace.tools.models import (GetDependenciesInput, GetMetricsInput,
    GetRecentDeploymentsInput, SearchLogsInput, SearchRunbooksInput)
from signal_trace.agent.models import (AgentState, Assessment, Evidence, InvestigationResult, InvestigationRun,
    ToolCall, ToolResult, Transition, ReliabilityIssue)
from signal_trace.agent.provider import InvestigationModel
from signal_trace.agent.reference import OfflineReferenceModel
from signal_trace.agent.observability import InvestigationTracer
from signal_trace.tools.models import LogRecord, MetricRecord, DeploymentRecord, Dependencies, RunbookRecord

TOOL_INPUTS = {
    'search_logs': SearchLogsInput,
    'get_metrics': GetMetricsInput,
    'get_recent_deployments': GetRecentDeploymentsInput,
    'get_dependencies': GetDependenciesInput,
    'search_runbooks': SearchRunbooksInput,
}


TOOL_OUTPUTS = dict(zip(TOOL_INPUTS, (LogRecord, MetricRecord, DeploymentRecord, Dependencies, RunbookRecord)))
OPERATIONAL = {'search_logs', 'get_metrics', 'get_recent_deployments'}


class DeadlineExceeded(TimeoutError):
    """The caller stopped waiting; the read-only worker may still be finishing."""


def bounded_call(function, timeout):
    # Daemon workers cannot mutate AgentState. Unlike an executor context manager,
    # this does not wait for a stuck worker during shutdown.
    result = queue.Queue(maxsize=1)
    def work():
        try:
            result.put((True, function()))
        except Exception as exc:
            result.put((False, exc))
    threading.Thread(target=work, daemon=True).start()
    try:
        ok, value = result.get(timeout=timeout)
    except queue.Empty:
        raise DeadlineExceeded('Call exceeded its configured deadline') from None
    if not ok:
        raise value
    return value


def transient(exc):
    if isinstance(exc, DeadlineExceeded):
        return False  # Do not overlap retries with a worker still running.
    if isinstance(exc, HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    return isinstance(exc, (TimeoutError, ConnectionError)) or (
        isinstance(exc, URLError) and isinstance(exc.reason, (TimeoutError, ConnectionError)))


def validate(model, value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return model.model_validate_json(json.dumps(value, allow_nan=False))


class Investigator:
    def __init__(self, model: InvestigationModel, tools: ToolLayer | None = None,
                 max_iterations: int = 12, tool_timeout: float = 10,
                 model_timeout: float = 120, max_retries: int = 1, tracer: InvestigationTracer | None = None):
        for name, value, minimum in [('max_iterations', max_iterations, 1), ('max_retries', max_retries, 0)]:
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f'{name} must be an integer >= {minimum}')
        if max_retries > 3:
            raise ValueError('max_retries must not exceed 3')
        for value in (tool_timeout, model_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Timeouts must be finite positive numbers')
        self.model = model
        self.tools = tools if tools is not None else ToolLayer()
        self.max_iterations, self.max_retries = max_iterations, max_retries
        self.tool_timeout, self.model_timeout = tool_timeout, model_timeout
        self.tracer = tracer

    def _issue(self, state, phase, kind, message, attempt=1):
        state.reliability_issues.append(ReliabilityIssue(phase=phase, kind=kind,
            message=message, iteration=state.iteration_count, attempt=attempt))

    def _model_call(self, state, phase, schema, check=lambda value: None):
        for attempt in range(1, self.max_retries + 2):
            started = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            try:
                snapshot = state.model_copy(deep=True)
                value = bounded_call(lambda: getattr(self.model, phase)(snapshot), self.model_timeout)
                value = validate(schema, value)
                check(value)
                if self.tracer: self.tracer.llm_call(phase, attempt, started, "success")
                return value
            except Exception as exc:
                if self.tracer: self.tracer.llm_call(phase, attempt, started, "failed", exc)
                self._issue(state, phase, type(exc).__name__, str(exc), attempt)
                repairable = isinstance(exc, (ValueError, TypeError, AttributeError, KeyError))
                if not (transient(exc) or repairable) or attempt > self.max_retries:
                    gap = f'Model {phase} unavailable or invalid: {type(exc).__name__}'
                    if gap not in state.missing_information:
                        state.missing_information.append(gap)
                    return None

    def _assess(self, state):
        previous_assessment = state.assessment.model_copy(deep=True)
        def check(assessment):
            for h in assessment.hypotheses:
                refs = h.supporting_evidence + h.contradicting_evidence
                if not set(refs) <= state.evidence.keys():
                    raise ValueError('Model cited evidence that no tool returned')
                if not any(state.evidence[r].tool in OPERATIONAL for r in h.supporting_evidence):
                    raise ValueError('Operational hypotheses require operational evidence')
                old = next((old for old in state.assessment.hypotheses if old.hypothesis_id == h.hypothesis_id), None)
                if old and set(h.supporting_evidence) & set(old.contradicting_evidence):
                    raise ValueError('Previously contradicting evidence must be retained')
                if set(h.supporting_evidence) & set(h.contradicting_evidence):
                    raise ValueError('Evidence cannot both support and contradict the same hypothesis')
        assessment = self._model_call(state, 'assess', Assessment, check)
        if assessment is None:
            assessment = state.assessment.model_copy(deep=True)
            assessment.sufficient_evidence = False
        # Preserve previously observed counterevidence even if a later model omits it.
        previous = {h.hypothesis_id: h for h in state.assessment.hypotheses}
        for h in assessment.hypotheses:
            old = previous.get(h.hypothesis_id)
            if old:
                h.contradicting_evidence = list(dict.fromkeys(old.contradicting_evidence + h.contradicting_evidence))
                h.supporting_evidence = [r for r in dict.fromkeys(old.supporting_evidence + h.supporting_evidence)
                                         if r not in h.contradicting_evidence]
        for key, old in previous.items():
            if key not in {h.hypothesis_id for h in assessment.hypotheses} and old.contradicting_evidence:
                assessment.hypotheses.append(old.model_copy(deep=True))
        important_missing = any(r.status in ('failed', 'invalid')
                                or r.status == 'empty' and r.call.name in OPERATIONAL
                                and r.call.arguments.get('service') == state.incident.alert.service_id
                                for r in state.tool_results)
        important_missing = important_missing or any(g.startswith('Unknown dependency topology') for g in state.missing_information)
        important_missing = important_missing or any(r.call.name == 'get_dependencies' and r.status == 'empty'
                                                      for r in state.tool_results)
        for h in assessment.hypotheses:
            if important_missing or h.contradicting_evidence:
                h.confidence = min(h.confidence, 0.6)
        primary = next((h for h in assessment.hypotheses if h.hypothesis_id == assessment.primary_hypothesis_id), None)
        independent_tools = {state.evidence[r].tool for r in primary.supporting_evidence
                             if state.evidence[r].tool in OPERATIONAL} if primary else set()
        if primary and len(independent_tools) < 2:
            primary.confidence = min(primary.confidence, .6)
        if primary and primary.contradicting_evidence:
            assessment.missing_information.append('Resolve conflicting evidence for the primary hypothesis')
        if len(independent_tools) < 2 or not primary or primary.confidence < .8 or important_missing or primary.contradicting_evidence:
            assessment.sufficient_evidence = False
        assessment.missing_information = list(dict.fromkeys(assessment.missing_information + state.missing_information))
        state.assessment = assessment
        state.current_confidence = primary.confidence if primary else 0.0
        state.history.append(Transition(iteration=state.iteration_count,
            evidence_count=len(state.evidence), assessment=assessment.model_copy(deep=True)))
        if self.tracer: self.tracer.assessment(state.iteration_count, previous_assessment, assessment, len(state.evidence))

    def _execute(self, state, call, arguments):
        before_evidence = set(state.evidence)
        started = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        started_mono = __import__("time").perf_counter()
        call_id = f'call-{state.iteration_count}'
        result = ToolResult(call_id=call_id, call=call, records=[], evidence_ids=[])
        for attempt in range(1, self.max_retries + 2):
            result.attempts = attempt
            try:
                returned = bounded_call(lambda: getattr(self.tools, call.name)(**arguments.model_dump()), self.tool_timeout)
            except Exception as exc:
                self._issue(state, call.name, type(exc).__name__, str(exc), attempt)
                if transient(exc) and attempt <= self.max_retries:
                    continue
                result.status, result.error = 'failed', f'{type(exc).__name__}: {exc}'
                break
            try:
                if returned is None:
                    records = []
                elif call.name == 'get_dependencies':
                    records = [returned]
                elif isinstance(returned, list):
                    records = returned
                else:
                    raise ValueError('Tool must return a list of records')
                # Validate the entire response before committing any evidence.
                payloads = [validate(TOOL_OUTPUTS[call.name], r).model_dump(mode='json') for r in records]
                if call.name == 'get_dependencies' and payloads and not payloads[0]['service_known']:
                    state.missing_information.append(f'Unknown dependency topology for {arguments.service}')
                for payload in payloads:
                    if call.name != 'search_runbooks' and payload['service'] != arguments.service:
                        raise ValueError('Tool returned a different service')
                result.records = payloads
                result.status = 'success' if payloads else 'empty'
            except Exception as exc:
                self._issue(state, call.name, 'invalid_response', str(exc), attempt)
                result.status, result.error = 'invalid', str(exc)
            break
        if result.status != 'success':
            gap = f'{call.name} {json.dumps(call.arguments, sort_keys=True)}: {result.status}'
            state.missing_information.append(gap)
            if result.status == 'empty':
                self._issue(state, call.name, 'empty', gap)
        for payload in result.records:
            digest = hashlib.sha256(json.dumps([call.name, payload], sort_keys=True).encode()).hexdigest()[:20]
            evidence_id = f'ev-{digest}'
            source_id = next((str(payload[k]) for k in ('log_id', 'metric_id', 'deployment_id', 'chunk_id', 'runbook_id')
                              if payload.get(k)), f"topology:{payload.get('service')}")
            if evidence_id not in state.evidence:
                state.evidence[evidence_id] = Evidence(evidence_id=evidence_id, source_id=source_id,
                    tool=call.name, record=payload, call_ids=[])
            if call_id not in state.evidence[evidence_id].call_ids:
                state.evidence[evidence_id].call_ids.append(call_id)
            result.evidence_ids.append(evidence_id)
        state.tool_results.append(result)
        if self.tracer: self.tracer.tool_call(call, arguments, started, result, before_evidence, set(state.evidence), started_mono)

    def run(self, incident: IncidentRequest) -> InvestigationRun:
        if self.tracer is None: self.tracer = InvestigationTracer()
        self.tracer.start(incident)
        state = AgentState(incident=incident.model_copy(deep=True))
        seen = set()
        self._assess(state)
        while state.iteration_count < self.max_iterations:
            if state.assessment.sufficient_evidence:
                state.stop_reason = 'sufficient_evidence'
                break
            state.iteration_count += 1
            def check(call):
                TOOL_INPUTS[call.name].model_validate_json(json.dumps(call.arguments))
            call = self._model_call(state, 'select_tool', ToolCall, check)
            if call is None:
                state.stop_reason = 'model_failure'
                break
            arguments = TOOL_INPUTS[call.name].model_validate_json(json.dumps(call.arguments))
            key = (call.name, json.dumps(arguments.model_dump(mode='json'), sort_keys=True))
            if key in seen:
                message = f'Repeated {call.name} call suppressed; no new evidence requested'
                self._issue(state, call.name, 'duplicate_call', message)
                state.missing_information.append(message)
                skipped = ToolResult(call_id=f'call-{state.iteration_count}', call=call,
                    records=[], evidence_ids=[], status='skipped', attempts=0, error=message)
                state.tool_results.append(skipped)
                if self.tracer:
                    self.tracer.tool_call(call, arguments, __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), skipped, set(state.evidence), set(state.evidence))
                state.stop_reason = 'no_progress'
                break
            seen.add(key)
            self._execute(state, call, arguments)
            self._assess(state)
        if state.stop_reason is None:
            state.stop_reason = 'sufficient_evidence' if state.assessment.sufficient_evidence else 'iteration_limit'
        if state.stop_reason != 'sufficient_evidence':
            state.assessment.sufficient_evidence = False
            state.missing_information.append(f'Investigation stopped: {state.stop_reason}; evidence is insufficient')
        state.assessment.missing_information = list(dict.fromkeys(state.assessment.missing_information + state.missing_information))
        state.missing_information = list(state.assessment.missing_information)
        def check_final(result):
            if result.incident_id != incident.incident_id:
                raise ValueError('Final result changed incident identity')
            for evidence in result.supporting_evidence:
                if state.evidence.get(evidence.evidence_id) != evidence:
                    raise ValueError('Final result changed tool evidence')
            primary = next((h for h in state.assessment.hypotheses if h.hypothesis_id == state.assessment.primary_hypothesis_id), None)
            alternatives = [h for h in state.assessment.hypotheses if h != primary]
            if result.primary_hypothesis != primary or result.alternative_hypotheses != alternatives:
                raise ValueError('Final hypotheses must preserve the last assessment')
            if result.missing_information != state.assessment.missing_information:
                raise ValueError('Final result must preserve missing information')
        result = self._model_call(state, 'finalize', InvestigationResult, check_final)
        if result is None:
            state.assessment.missing_information = list(dict.fromkeys(state.assessment.missing_information + state.missing_information))
            state.missing_information = list(state.assessment.missing_information)
            state.assessment.sufficient_evidence = False
            state.stop_reason = 'model_failure'
            for hypothesis in state.assessment.hypotheses:
                hypothesis.confidence = min(hypothesis.confidence, .6)
            primary = next((h for h in state.assessment.hypotheses
                            if h.hypothesis_id == state.assessment.primary_hypothesis_id), None)
            state.current_confidence = primary.confidence if primary else 0.0
            result = OfflineReferenceModel().finalize(state.model_copy(deep=True))
        result.supporting_evidence = [e.model_copy(deep=True) for e in state.evidence.values()]
        result.evidence_ids = list(state.evidence)
        result.outcome = 'sufficient_evidence' if state.assessment.sufficient_evidence else 'insufficient_evidence'
        if result.outcome == 'insufficient_evidence':
            result.summary = 'Insufficient evidence for a confirmed cause. ' + result.summary
            result.recommended_actions = ['Collect the recorded missing information before choosing a mitigation.']
        final = InvestigationResult.model_validate(result.model_dump())
        self.tracer.finish(state, final)
        return InvestigationRun(model=self.model.name, state=state, result=final, trace=self.tracer.snapshot())
