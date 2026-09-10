"""Incident acceptance only; no persistence or triage execution."""

from fastapi import APIRouter

from signal_trace.models.incident import IncidentRequest
from signal_trace.models.responses import IncidentAcknowledgement

router = APIRouter(tags=["incidents"])


@router.post("/incidents", response_model=IncidentAcknowledgement)
def accept_incident(incident: IncidentRequest) -> IncidentAcknowledgement:
    return IncidentAcknowledgement(incident_id=incident.incident_id)
