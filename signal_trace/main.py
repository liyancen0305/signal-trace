"""Compatibility entry point for uvicorn signal_trace.main:app."""

from signal_trace.api.app import app, create_app

__all__ = ["app", "create_app"]
