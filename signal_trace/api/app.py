"""ASGI entry point: uvicorn signal_trace.api.app:app."""

from fastapi import FastAPI

from signal_trace.api import health, incidents
from signal_trace.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    application = FastAPI(title=settings.app_name, version="0.1.0")
    application.include_router(health.router)
    application.include_router(incidents.router)
    return application


app = create_app()
