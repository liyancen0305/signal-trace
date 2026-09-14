"""Real Part 4 → Part 5 integration; requires a disposable pgvector database.

Instrumentation delegates to real embeddings and SQL. Only forbidden fallback and
in-investigation writes are blocked. Ingestion is an explicit setup operation.
"""
import inspect
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg

from signal_trace.agent.runtime import configured_investigator
from signal_trace.config import Settings
from signal_trace.models.incident import IncidentRequest
from signal_trace.retrieval.embeddings import FastEmbedProvider
from signal_trace.retrieval.ingestion import ingest_runbooks
from signal_trace.retrieval.runtime import configured_retriever
from signal_trace.retrieval.service import SemanticRetriever
from signal_trace.retrieval.storage import PgVectorStore
from signal_trace.tools.source import SyntheticSource

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get('SIGNAL_TRACE_TEST_DATABASE_URL')


def semantic_smoke(database_url):
    """Return the real investigation and an assertion-backed path audit."""
    incident = IncidentRequest.model_validate_json(
        (ROOT / 'scenarios/uc1_deployment_5xx/incident.json').read_text())
    settings = Settings(agent_provider='offline', retrieval_database_url=database_url)
    embedder = FastEmbedProvider(settings.embedding_model, settings.embedding_cache_dir)
    store = PgVectorStore(database_url)
    store.initialize()
    count = ingest_runbooks(store, embedder)
    with psycopg.connect(database_url) as conn:
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    audit = {'pgvector_version': version, 'ingested_chunks': count,
             'embedding_model': embedder.model_id, 'dimensions': embedder.dimensions,
             'semantic_calls': [], 'query_embeddings': [], 'vector_searches': [], 'sql': []}
    configured_retriever.cache_clear()
    real_semantic = SemanticRetriever.search
    real_embed = FastEmbedProvider.embed_query
    real_search = PgVectorStore.search
    real_execute = psycopg.Connection.execute
    real_connect = psycopg.connect
    real_open = io.open

    def semantic(self, query, service=None, top_k=5):
        assert any(frame.filename.endswith('tools/service.py') and frame.function == 'search_runbooks'
                   for frame in inspect.stack()), 'Semantic retrieval bypassed Tool Layer'
        result = real_semantic(self, query, service, top_k)
        audit['semantic_calls'].append({'query': query, 'service': service, 'top_k': top_k,
                                       'results': [r.model_dump(mode='json') for r in result]})
        return result

    def embed(self, query):
        vector = real_embed(self, query)
        audit['query_embeddings'].append({'query': query, 'dimensions': len(vector), 'model': self.model_id})
        return vector

    def search(self, vector, model_id, service, top_k, min_score):
        result = real_search(self, vector, model_id, service, top_k, min_score)
        audit['vector_searches'].append({'dimensions': len(vector), 'model': model_id,
            'service': service, 'top_k': top_k, 'min_score': min_score, 'results': len(result)})
        return result

    def connect(*args, **kwargs):
        assert inspect.currentframe().f_back.f_globals['__name__'] == 'signal_trace.retrieval.storage', 'Direct database access'
        return real_connect(*args, **kwargs)

    def execute(self, query, *args, **kwargs):
        assert inspect.currentframe().f_back.f_globals['__name__'] == 'signal_trace.retrieval.storage', 'Direct SQL access'
        assert self.read_only, 'Search must use a read-only connection'
        result = real_execute(self, query, *args, **kwargs)
        audit['sql'].append(str(query))
        return result

    def open_file(file, *args, **kwargs):
        if isinstance(file, (str, Path)):
            parts = Path(file).resolve().parts
            assert 'evaluation' not in parts and 'runbooks' not in parts, 'Forbidden file read during investigation'
        return real_open(file, *args, **kwargs)

    try:
        # Exercise configuration-based selection, not an injected fake retriever.
        with patch.dict(os.environ, {'SIGNAL_TRACE_RETRIEVAL_DATABASE_URL': database_url}), \
             patch.object(SemanticRetriever, 'search', semantic), \
             patch.object(FastEmbedProvider, 'embed_query', embed), \
             patch.object(PgVectorStore, 'search', search), \
             patch('psycopg.connect', connect), \
             patch.object(psycopg.Connection, 'execute', execute), \
             patch('io.open', open_file), \
             patch('builtins.open', open_file), \
             patch.object(SyntheticSource, 'runbooks', side_effect=AssertionError('Keyword fallback forbidden')) as fallback, \
             patch.object(PgVectorStore, 'initialize', side_effect=AssertionError('No initialization during investigation')), \
             patch.object(PgVectorStore, 'replace', side_effect=AssertionError('No ingestion during investigation')):
            run = configured_investigator(settings).run(incident)
            fallback.assert_not_called()
    finally:
        configured_retriever.cache_clear()

    assert len(audit['semantic_calls']) == len(audit['query_embeddings']) == len(audit['vector_searches']) == 1
    assert any('embedding <=>' in sql for sql in audit['sql']), 'pgvector cosine SQL was not executed'
    rows = audit['semantic_calls'][0]['results']
    assert rows and all(r['document_id'] == 'rb-deployment-5xx' for r in rows)
    assert all(r['source'] == 'runbook:rb-deployment-5xx' for r in rows)
    call = next(r for r in run.state.tool_results if r.call.name == 'search_runbooks')
    assert call.call.arguments['query'] == audit['query_embeddings'][0]['query']
    assert len(call.records) == len(rows)
    for row, ref, record in zip(rows, call.evidence_ids, call.records, strict=True):
        evidence = run.state.evidence[ref]
        assert evidence.source_id == row['chunk_id']
        assert evidence.tool == 'search_runbooks' and call.call_id in evidence.call_ids
        assert evidence.record == record
        assert record['text'] == row['text'] and record['score'] == row['score']
        assert record['source'] == row['source'] and record['metadata'] == row['metadata']
        assert evidence in run.result.supporting_evidence
    assert run.result.affected_services == ['checkout-service']
    assert run.result.primary_hypothesis.hypothesis_id == 'deployment-regression'
    assert run.result.severity == 'SEV-2'
    assert run.state.stop_reason == 'sufficient_evidence'
    audit.update(keyword_fallback_used=False, provenance_verified=True,
                 database_access_via_retrieval_only=True,
                 raw_runbook_and_evaluation_reads_during_investigation=False)
    return run, audit


@unittest.skipUnless(DATABASE_URL and os.environ.get('SIGNAL_TRACE_TEST_REAL_EMBEDDINGS') == '1',
                     'Enable a disposable database and real embeddings for Agent semantic integration')
class AgentSemanticIntegrationTests(unittest.TestCase):
    def test_configured_semantic_retrieval_enters_agent_state_without_fallback(self):
        semantic_smoke(DATABASE_URL)
