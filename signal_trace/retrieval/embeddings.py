"""Replaceable embedding interface and isolated local FastEmbed provider."""
from math import isfinite
from pathlib import Path
from typing import Protocol

from signal_trace.retrieval.models import RetrievalError


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def validate_vector(vector: list[float], dimensions: int) -> None:
    if len(vector) != dimensions or not all(isfinite(v) for v in vector) or not any(vector):
        raise RetrievalError('Embedding must be finite, nonzero, and match the configured dimensions')


class FastEmbedProvider:
    """CPU embeddings; no generative model or remote inference API calls."""

    def __init__(self, model_name: str = 'BAAI/bge-small-en-v1.5', cache_dir: Path | None = None):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RetrievalError('Install signal-trace[retrieval] to enable local semantic embeddings') from exc
        supported = {item['model']: item for item in TextEmbedding.list_supported_models()}
        if model_name not in supported:
            raise RetrievalError('Unsupported embedding model')
        self.model_id = f'fastembed:{model_name}'
        self.dimensions = supported[model_name]['dim']
        self._model_name = model_name
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._model = None

    def _get_model(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir, threads=1)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return [row.tolist() for row in self._get_model().passage_embed(texts)]
        except Exception as exc:
            raise RetrievalError('Local document embedding failed') from exc

    def embed_query(self, text: str) -> list[float]:
        try:
            return next(self._get_model().query_embed(text)).tolist()
        except Exception as exc:
            raise RetrievalError('Local query embedding failed') from exc
