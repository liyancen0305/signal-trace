"""Bounded, model-directed loop. All operational access goes through ToolLayer."""
import hashlib
import json

from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer
from signal_trace.tools.models import (GetDependenciesInput, GetMetricsInput,
    GetRecentDeploymentsInput, SearchLogsInput, SearchRunbooksInput)
from signal_trace.agent.models import (AgentState, Assessment, Evidence, InvestigationResult, InvestigationRun,
    ToolCall, ToolResult, Transition)
from signal_trace.agent.provider import InvestigationModel

TOOL_INPUTS = {
    'search_logs': SearchLogsInput,
    'get_metrics': GetMetricsInput,
    'get_recent_deployments': GetRecentDeploymentsInput,
    'get_dependencies': GetDependenciesInput,
    'search_runbooks': SearchRunbooksInput,
}


class Investigator:
    def __init__(self, model: InvestigationModel, tools: ToolLayer | None = None,
                 max_iterations: int = 12):
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
            raise ValueError('max_iterations must be a positive integer')
        self.model = model
        self.tools = tools if tools is not None else ToolLayer()
        self.max_iterations = max_iterations

    def _assess(self, state: AgentState) -> None:
        assessment = self.model.assess(state.model_copy(deep=True))
        # Validate even injected providers; no provider owns mutable runtime state.
        assessment = Assessment.model_validate(assessment.model_dump())
        for h in assessment.hypotheses:
            refs = h.supporting_evidence + h.contradicting_evidence
            if not set(refs) <= state.evidence.keys():
                raise ValueError('Model cited evidence that no tool returned')
            if not any(state.evidence[r].tool in ('search_logs', 'get_metrics', 'get_recent_deployments')
                       for r in h.supporting_evidence):
                raise ValueError('Operational hypotheses require operational evidence')
        state.assessment = assessment
        primary = next((h for h in assessment.hypotheses
                        if h.hypothesis_id == assessment.primary_hypothesis_id), None)
        state.current_confidence = primary.confidence if primary else 0.0
        state.history.append(Transition(iteration=state.iteration_count,
            evidence_count=len(state.evidence), assessment=assessment.model_copy(deep=True)))

    def run(self, incident: IncidentRequest) -> InvestigationRun:
        state = AgentState(incident=incident.model_copy(deep=True))
        self._assess(state)
        while state.iteration_count < self.max_iterations:
            if state.assessment.sufficient_evidence:
                state.stop_reason = 'sufficient_evidence'
                break
            call = ToolCall.model_validate(self.model.select_tool(state.model_copy(deep=True)).model_dump())
            # JSON validation accepts ISO timestamps while retaining strict field types.
            arguments = TOOL_INPUTS[call.name].model_validate_json(json.dumps(call.arguments))
            returned = getattr(self.tools, call.name)(**arguments.model_dump())
            records = returned if isinstance(returned, list) else [returned]
            call_id = f'call-{state.iteration_count + 1}'
            ids, payloads = [], []
            for record in records:
                payload = record.model_dump(mode='json')
                payloads.append(payload)
                digest = hashlib.sha256(json.dumps([call.name, payload], sort_keys=True).encode()).hexdigest()[:20]
                evidence_id = f'ev-{digest}'
                source_id = next((str(payload[k]) for k in
                    ('log_id', 'metric_id', 'deployment_id', 'chunk_id', 'runbook_id') if payload.get(k)),
                    f"topology:{payload.get('service')}")
                if evidence_id not in state.evidence:
                    state.evidence[evidence_id] = Evidence(evidence_id=evidence_id,
                        source_id=source_id, tool=call.name, record=payload, call_ids=[])
                if call_id not in state.evidence[evidence_id].call_ids:
                    state.evidence[evidence_id].call_ids.append(call_id)
                ids.append(evidence_id)
            state.tool_results.append(ToolResult(call_id=call_id, call=call,
                records=payloads, evidence_ids=ids))
            state.iteration_count += 1
            self._assess(state)
        if state.stop_reason is None:
            state.stop_reason = 'sufficient_evidence' if state.assessment.sufficient_evidence else 'iteration_limit'
        result = InvestigationResult.model_validate(self.model.finalize(state.model_copy(deep=True)).model_dump())
        if result.incident_id != incident.incident_id:
            raise ValueError('Final result changed incident identity')
        for evidence in result.supporting_evidence:
            if state.evidence.get(evidence.evidence_id) != evidence:
                raise ValueError('Final result changed tool evidence')
        primary = next((h for h in state.assessment.hypotheses
                        if h.hypothesis_id == state.assessment.primary_hypothesis_id), None)
        alternatives = [h for h in state.assessment.hypotheses if h != primary]
        if result.primary_hypothesis != primary or result.alternative_hypotheses != alternatives:
            raise ValueError('Final hypotheses must preserve the last assessment')
        if result.missing_information != state.assessment.missing_information:
            raise ValueError('Final result must preserve missing information')
        return InvestigationRun(model=self.model.name, state=state, result=result)
