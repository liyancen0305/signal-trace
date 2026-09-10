"""Configuration wiring; no database writes or model downloads at import time."""
from functools import lru_cache
from pathlib import Path

from signal_trace.retrieval.embeddings import FastEmbedProvider
from signal_trace.retrieval.service import SemanticRetriever
from signal_trace.retrieval.storage import PgVectorStore


@lru_cache(maxsize=4)
def configured_retriever(database_url: str, model_name: str, cache_dir: Path, min_score: float):
    return SemanticRetriever(FastEmbedProvider(model_name, cache_dir), PgVectorStore(database_url), min_score)
