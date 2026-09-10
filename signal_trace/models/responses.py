"""Current acknowledgements and the contract for a future triage result."""

from typing import Literal

from signal_trace.models.common import APIModel, NonEmptyString


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"


class IncidentAcknowledgement(APIModel):
    incident_id: NonEmptyString
    status: Literal["accepted"] = "accepted"
    message: str = "Incident accepted. Triage is not implemented."


class TriageResponse(APIModel):
    """Future output only; no endpoint produces this model in Part 2.

    Evidence IDs refer to existing log, metric, or deployment records.
    This contract does not validate references against evaluation data.
    """

    schema_version: Literal["1.0"] = "1.0"
    incident_id: NonEmptyString
    summary: NonEmptyString
    root_cause: NonEmptyString | None = None
    affected_service: NonEmptyString | None = None
    severity: NonEmptyString | None = None
    evidence_ids: list[NonEmptyString]
