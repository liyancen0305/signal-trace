"""Read-only semantic retrieval orchestration, without agent or reasoning logic."""
from math import isfinite
from typing import Protocol

from signal_trace.retrieval.embeddings import EmbeddingProvider, validate_vector
from signal_trace.retrieval.models import SearchRequest, SearchResult
from signal_trace.retrieval.storage import VectorReader


class Retriever(Protocol):
    def search(self, query: str, service: str | None = None, top_k: int = 5) -> list[SearchResult]: ...


class SemanticRetriever:
    def __init__(self, embedder: EmbeddingProvider, store: VectorReader, min_score: float = 0.55):
        if not isfinite(min_score) or not -1 <= min_score <= 1:
            raise ValueError('min_score must be finite and between -1 and 1')
        self._embedder = embedder
        self._store = store
        self._min_score = min_score

    def search(self, query: str, service: str | None = None, top_k: int = 5) -> list[SearchResult]:
        request = SearchRequest(query=query, service=service, top_k=top_k)
        if not any(char.isalnum() for char in request.query):
            return []
        vector = self._embedder.embed_query(request.query)
        validate_vector(vector, self._embedder.dimensions)
        return self._store.search(vector, self._embedder.model_id, request.service,
                                  request.top_k, self._min_score)
