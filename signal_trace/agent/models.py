"""Typed investigation state, model decisions, and public result contracts."""
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from signal_trace.models.common import APIModel, NonEmptyString
from signal_trace.models.incident import IncidentRequest
from signal_trace.models.responses import TriageResponse

ToolName = Literal['search_logs', 'get_metrics', 'get_recent_deployments',
                   'get_dependencies', 'search_runbooks']


class ToolCall(APIModel):
    name: ToolName
    arguments: dict[str, JsonValue]
    reason: NonEmptyString


class Evidence(APIModel):
    evidence_id: NonEmptyString
    source_id: NonEmptyString
    tool: ToolName
    record: dict[str, JsonValue]
    call_ids: list[str]


class ToolResult(APIModel):
    call_id: str
    call: ToolCall
    records: list[dict[str, JsonValue]]
    evidence_ids: list[str]
    status: Literal["success", "empty", "failed", "invalid", "skipped"] = "success"
    attempts: int = 1
    error: str | None = None


class Hypothesis(APIModel):
    hypothesis_id: NonEmptyString
    description: NonEmptyString
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    supporting_evidence: list[str] = Field(min_length=1)
    contradicting_evidence: list[str] = Field(default_factory=list)


class Assessment(APIModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    primary_hypothesis_id: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    sufficient_evidence: bool = False
    rationale: NonEmptyString

    @model_validator(mode='after')
    def primary_exists(self):
        ids = [h.hypothesis_id for h in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError('Hypothesis IDs must be unique')
        if self.primary_hypothesis_id is not None and self.primary_hypothesis_id not in ids:
            raise ValueError('Primary hypothesis must exist')
        if self.sufficient_evidence and self.primary_hypothesis_id is None:
            raise ValueError('Sufficient evidence requires a primary hypothesis')
        return self


class Transition(APIModel):
    iteration: int
    evidence_count: int
    assessment: Assessment


class ReliabilityIssue(APIModel):
    phase: str
    kind: str
    message: str
    iteration: int
    attempt: int = 1


class AgentState(APIModel):
    incident: IncidentRequest
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    tool_results: list[ToolResult] = Field(default_factory=list)
    assessment: Assessment = Field(default_factory=lambda: Assessment(rationale='Not yet assessed'))
    missing_information: list[str] = Field(default_factory=list)
    reliability_issues: list[ReliabilityIssue] = Field(default_factory=list)
    iteration_count: int = 0
    current_confidence: float = Field(default=0, ge=0, le=1)
    history: list[Transition] = Field(default_factory=list)
    stop_reason: Literal['sufficient_evidence', 'iteration_limit', 'insufficient_evidence', 'model_failure', 'no_progress'] | None = None


class InvestigationResult(TriageResponse):
    """Additive Part 5 extension of the Part 2 output contract."""
    outcome: Literal["sufficient_evidence", "insufficient_evidence"] = "insufficient_evidence"
    severity: NonEmptyString
    severity_rationale: NonEmptyString
    affected_services: list[NonEmptyString]
    primary_hypothesis: Hypothesis | None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    supporting_evidence: list[Evidence]
    alternative_hypotheses: list[Hypothesis]
    missing_information: list[str]
    recommended_actions: list[NonEmptyString]

    @model_validator(mode='after')
    def consistent(self):
        ids = [e.evidence_id for e in self.supporting_evidence]
        if len(ids) != len(set(ids)) or self.evidence_ids != ids:
            raise ValueError('Evidence IDs must match the supplied evidence')
        hypotheses = self.alternative_hypotheses + ([self.primary_hypothesis] if self.primary_hypothesis else [])
        for h in hypotheses:
            if not set(h.supporting_evidence + h.contradicting_evidence) <= set(ids):
                raise ValueError('Hypothesis references unavailable evidence')
        if self.primary_hypothesis:
            if self.root_cause != self.primary_hypothesis.description or self.confidence != self.primary_hypothesis.confidence:
                raise ValueError('Root cause and confidence must match the primary hypothesis')
        elif self.root_cause is not None or self.confidence != 0:
            raise ValueError('No primary hypothesis means no root cause and zero confidence')
        if self.affected_service is not None and self.affected_service not in self.affected_services:
            raise ValueError('Affected service must appear in affected_services')
        return self


class InvestigationRun(APIModel):
    model: str
    state: AgentState
    result: InvestigationResult
    trace: dict[str, JsonValue] = Field(default_factory=dict)
