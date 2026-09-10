"""Service liveness endpoint."""

from fastapi import APIRouter

from signal_trace.models.responses import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
