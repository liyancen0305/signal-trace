"""Vector storage interfaces and PostgreSQL/pgvector implementation."""
from importlib.resources import files
import json
from typing import Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from signal_trace.retrieval.embeddings import validate_vector
from signal_trace.retrieval.models import Chunk, RetrievalError, SearchResult


class VectorReader(Protocol):
    def search(self, vector: list[float], model_id: str, service: str | None,
               top_k: int, min_score: float) -> list[SearchResult]: ...


class VectorWriter(Protocol):
    def replace(self, chunks: list[Chunk], vectors: list[list[float]],
                model_id: str, dimensions: int) -> None: ...


class PgVectorStore:
    """One dedicated operational knowledge corpus per database.

    Search opens read-only repeatable-read transactions. Schema creation and snapshot
    replacement are explicit ingestion operations and are never invoked by search.
    """
    def __init__(self, database_url: str):
        self._database_url = database_url

    def initialize(self) -> None:
        try:
            with psycopg.connect(self._database_url, connect_timeout=5) as conn:
                conn.execute(files('signal_trace.retrieval').joinpath('schema.sql').read_text())
        except psycopg.Error as exc:
            raise RetrievalError('Knowledge storage initialization failed') from exc

    def replace(self, chunks: list[Chunk], vectors: list[list[float]],
                model_id: str, dimensions: int) -> None:
        if len(chunks) != len(vectors) or len({c.chunk_id for c in chunks}) != len(chunks):
            raise RetrievalError('Invalid chunk/embedding batch')
        if not model_id or isinstance(dimensions, bool) or dimensions < 1:
            raise RetrievalError('Invalid embedding configuration')
        for vector in vectors:
            validate_vector(vector, dimensions)
        try:
            with psycopg.connect(self._database_url, connect_timeout=5) as conn:
                conn.execute('SELECT pg_advisory_xact_lock(73902104)')
                conn.execute('DELETE FROM signal_trace_knowledge_chunks')
                conn.execute('''INSERT INTO signal_trace_knowledge_config (singleton, model_id, dimensions)
                                VALUES (TRUE, %s, %s) ON CONFLICT (singleton)
                                DO UPDATE SET model_id = EXCLUDED.model_id, dimensions = EXCLUDED.dimensions''',
                             (model_id, dimensions))
                with conn.cursor() as cursor:
                    cursor.executemany('''INSERT INTO signal_trace_knowledge_chunks
                        (chunk_id, document_id, source, services, content, embedding, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)''',
                        [(c.chunk_id, c.document_id, c.source, c.services, c.text,
                          json.dumps(v), Jsonb(c.metadata)) for c, v in zip(chunks, vectors)])
        except psycopg.Error as exc:
            raise RetrievalError('Knowledge ingestion failed; previous corpus retained') from exc

    def search(self, vector: list[float], model_id: str, service: str | None,
               top_k: int, min_score: float) -> list[SearchResult]:
        validate_vector(vector, len(vector))
        try:
            with psycopg.connect(self._database_url, connect_timeout=5, row_factory=dict_row) as conn:
                conn.read_only = True
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                config = conn.execute('SELECT model_id, dimensions FROM signal_trace_knowledge_config').fetchone()
                if config is None:
                    raise RetrievalError('Knowledge corpus is not ingested')
                if config['model_id'] != model_id or config['dimensions'] != len(vector):
                    raise RetrievalError('Embedding configuration changed; re-ingest the knowledge corpus')
                rows = conn.execute('''SELECT chunk_id, document_id, source, services,
                    content AS text, metadata, 1 - (embedding <=> %s::vector) AS score
                    FROM signal_trace_knowledge_chunks
                    WHERE (%s::text IS NULL OR %s = ANY(services))
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector, chunk_id
                    LIMIT %s''',
                    (json.dumps(vector), service, service, json.dumps(vector), min_score,
                     json.dumps(vector), top_k)).fetchall()
                return [SearchResult(**{**row, 'score': max(-1.0, min(1.0, row['score']))}) for row in rows]
        except psycopg.Error as exc:
            raise RetrievalError('Knowledge search unavailable; check database and ingestion setup') from exc
