"""Configuration wiring shared by API and CLI."""
from signal_trace.agent.provider import OllamaModel
from signal_trace.agent.reference import OfflineReferenceModel
from signal_trace.agent.workflow import Investigator
from signal_trace.config import Settings


def configured_investigator(settings: Settings | None = None) -> Investigator:
    settings = settings if settings is not None else Settings()
    model = OfflineReferenceModel() if settings.agent_provider == 'offline' else OllamaModel(
        settings.agent_model or '', settings.agent_base_url, timeout=settings.agent_model_timeout)
    return Investigator(model, max_iterations=settings.agent_max_iterations,
        tool_timeout=settings.agent_tool_timeout, model_timeout=settings.agent_model_timeout,
        max_retries=settings.agent_max_retries)
