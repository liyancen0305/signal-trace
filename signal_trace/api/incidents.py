"""Stateless incident acceptance and synchronous read-only triage."""

from fastapi import APIRouter, Request

from signal_trace.agent.models import InvestigationResult
from signal_trace.agent.runtime import configured_investigator

from signal_trace.models.incident import IncidentRequest
from signal_trace.models.responses import IncidentAcknowledgement

router = APIRouter(tags=["incidents"])


@router.post("/incidents", response_model=IncidentAcknowledgement)
def accept_incident(incident: IncidentRequest) -> IncidentAcknowledgement:
    return IncidentAcknowledgement(incident_id=incident.incident_id)


@router.post("/incidents/triage", response_model=InvestigationResult)
def triage_incident(incident: IncidentRequest, request: Request) -> InvestigationResult:
    return configured_investigator(request.app.state.settings).run(incident).result
