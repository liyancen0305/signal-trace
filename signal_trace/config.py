"""Environment-based service configuration."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIGNAL_TRACE_")

    app_name: str = Field(default="Signal Trace", min_length=1)

    retrieval_database_url: SecretStr | None = Field(default=None, min_length=1)
    embedding_model: str = 'BAAI/bge-small-en-v1.5'
    embedding_cache_dir: Path = Path('.cache/embeddings')
    retrieval_min_score: float = Field(default=0.55, ge=-1, le=1, allow_inf_nan=False)
