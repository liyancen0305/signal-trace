"""Environment-based service configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIGNAL_TRACE_")

    app_name: str = Field(default="Signal Trace", min_length=1)
