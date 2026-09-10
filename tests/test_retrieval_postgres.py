"""Integration tests. Use a disposable database: these replace the knowledge corpus."""
import os
from pathlib import Path
import re
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

from signal_trace.retrieval.embeddings import FastEmbedProvider
from signal_trace.retrieval.ingestion import ingest_runbooks
from signal_trace.retrieval.models import Chunk, RetrievalError
from signal_trace.retrieval.service import SemanticRetriever
from signal_trace.retrieval.storage import PgVectorStore
from signal_trace.tools import ToolLayer

DATABASE_URL = os.environ.get('SIGNAL_TRACE_TEST_DATABASE_URL')


def chunk(chunk_id, text='operational guidance', services=None):
    return Chunk(chunk_id=chunk_id, document_id='test-document', source='runbook:test',
                 services=services or ['checkout-service'], text=text, metadata={'kind': 'runbook'})


@unittest.skipUnless(DATABASE_URL, 'Set SIGNAL_TRACE_TEST_DATABASE_URL to a disposable pgvector database')
class PostgresRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.store = PgVectorStore(DATABASE_URL)
        self.store.initialize()
        self.store.replace([chunk('a'), chunk('b', services=['payment-service']), chunk('c')],
                           [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], 'fixed', 2)

    def test_cosine_filter_top_k_and_tie_order(self):
        rows = self.store.search([1.0, 0.0], 'fixed', None, 1, .5)
        self.assertEqual([r.chunk_id for r in rows], ['a'])
        self.assertEqual(rows[0].score, 1.0)
        self.assertEqual(rows[0].source, 'runbook:test')
        self.assertEqual(rows[0].metadata, {'kind': 'runbook'})
        self.assertEqual([r.chunk_id for r in self.store.search([1.0, 0.0], 'fixed', 'payment-service', 5, .5)], ['b'])
        self.assertEqual(self.store.search([1.0, 0.0], 'fixed', "x' OR TRUE --", 5, .5), [])
        self.assertEqual(self.store.search([-1.0, 0.0], 'fixed', None, 5, .5), [])

    def test_reingestion_replaces_stale_chunks_and_empty_corpus(self):
        self.store.replace([chunk('new')], [[1.0, 0.0]], 'fixed', 2)
        self.assertEqual([r.chunk_id for r in self.store.search([1.0, 0.0], 'fixed', None, 5, .5)], ['new'])
        self.store.replace([], [], 'fixed', 2)
        self.assertEqual(self.store.search([1.0, 0.0], 'fixed', None, 5, .5), [])

    def test_embedding_model_and_dimension_mismatch(self):
        with self.assertRaises(RetrievalError):
            self.store.search([1.0, 0.0], 'wrong-model', None, 5, .5)
        with self.assertRaises(RetrievalError):
            self.store.search([1.0, 0.0, 0.0], 'fixed', None, 5, .5)

    def test_failed_transaction_retains_previous_corpus(self):
        # Finite float64 value overflows pgvector's float32 storage and fails in SQL.
        with self.assertRaises(RetrievalError):
            self.store.replace([chunk('new')], [[1e100, 0.0]], 'new-model', 2)
        self.assertEqual([r.chunk_id for r in self.store.search([1.0, 0.0], 'fixed', None, 5, .5)], ['a', 'b'])

    def test_search_with_select_only_database_role(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute('''DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_trace_test_reader') THEN
                    CREATE ROLE signal_trace_test_reader NOLOGIN;
                END IF;
            END $$''')
            conn.execute('GRANT USAGE ON SCHEMA public TO signal_trace_test_reader')
            conn.execute('GRANT SELECT ON signal_trace_knowledge_config, signal_trace_knowledge_chunks TO signal_trace_test_reader')
        reader = PgVectorStore(make_conninfo(DATABASE_URL, options='-c role=signal_trace_test_reader'))
        self.assertEqual(len(reader.search([1.0, 0.0], 'fixed', None, 5, .5)), 2)
        with self.assertRaises(RetrievalError):
            reader.replace([], [], 'fixed', 2)


@unittest.skipUnless(DATABASE_URL and os.environ.get('SIGNAL_TRACE_TEST_REAL_EMBEDDINGS') == '1',
                     'Enable SIGNAL_TRACE_TEST_REAL_EMBEDDINGS=1 for downloaded model integration tests')
class RealSemanticRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = PgVectorStore(DATABASE_URL)
        cls.store.initialize()
        cls.embedder = FastEmbedProvider(cache_dir=Path('.cache/embeddings'))
        cls.count = ingest_runbooks(cls.store, cls.embedder)
        cls.retriever = SemanticRetriever(cls.embedder, cls.store)

    def test_real_semantic_match_without_exact_words(self):
        query = 'rollout broke purchasing'
        rows = self.retriever.search(query, 'checkout-service', top_k=2)
        self.assertTrue(rows)
        self.assertEqual(rows[0].document_id, 'rb-deployment-5xx')
        query_words = set(re.findall(r'\w+', query.casefold()))
        content_words = set(re.findall(r'\w+', rows[0].text.casefold()))
        self.assertFalse(query_words & content_words)

    def test_second_query_returns_operational_guidance(self):
        rows = self.retriever.search('How can I tell whether a problem comes from another service?', top_k=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any('dependency' in row.text.lower() for row in rows))
        self.assertEqual(rows[0].source, 'runbook:rb-deployment-5xx')

    def test_real_service_top_k_empty_and_tool_contract(self):
        self.assertEqual(self.count, 6)
        self.assertEqual(self.retriever.search('deployment failures', service='database'), [])
        self.assertEqual(self.retriever.search('deployment failures', service='unknown'), [])
        self.assertEqual(self.retriever.search('chocolate cake recipe and baking temperatures'), [])
        rows = ToolLayer(retriever=self.retriever).search_runbooks('deployment failures', top_k=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.chunk_id and row.text and row.score for row in rows))
        self.assertTrue(all('checkout-service' in row.services for row in rows))
