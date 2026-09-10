"""Deterministic retrieval tests, without model downloads or database requirements."""
from math import sqrt
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from signal_trace.retrieval.embeddings import validate_vector
from signal_trace.retrieval.ingestion import chunk_documents, ingest_runbooks, load_runbooks
from signal_trace.retrieval.models import Chunk, Document, RetrievalError, SearchResult
from signal_trace.retrieval.service import SemanticRetriever
from signal_trace.tools import ToolLayer


class FixedEmbeddings:
    """A test double only, not a semantic embedding implementation."""
    model_id = 'test-v1'
    dimensions = 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class MemoryVectors:
    """Test store implementing the same cosine score and ordering contract."""
    def __init__(self):
        self.chunks, self.vectors = [], []

    def replace(self, chunks, vectors, model_id, dimensions):
        self.chunks, self.vectors = chunks, vectors
        self.model_id, self.dimensions = model_id, dimensions

    def search(self, vector, model_id, service, top_k, min_score):
        matches = []
        for chunk, embedding in zip(self.chunks, self.vectors):
            if service is not None and service not in chunk.services:
                continue
            score = sum(a*b for a, b in zip(vector, embedding)) / (
                sqrt(sum(a*a for a in vector)) * sqrt(sum(b*b for b in embedding)))
            if score >= min_score:
                matches.append(SearchResult(**chunk.model_dump(), score=score))
        return sorted(matches, key=lambda r: (-r.score, r.chunk_id))[:top_k]


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.embedder = FixedEmbeddings()
        self.store = MemoryVectors()
        self.chunks = chunk_documents(load_runbooks())
        self.store.replace(self.chunks, self.embedder.embed_documents([c.text for c in self.chunks]), 'test-v1', 2)
        self.retriever = SemanticRetriever(self.embedder, self.store)

    def test_ingestion_and_metadata(self):
        count = ingest_runbooks(self.store, self.embedder)
        self.assertEqual(count, 6)
        for chunk in self.store.chunks:
            self.assertEqual(chunk.document_id, 'rb-deployment-5xx')
            self.assertEqual(chunk.source, 'runbook:rb-deployment-5xx')
            self.assertIn('checkout-service', chunk.services)
            self.assertEqual(chunk.metadata['kind'], 'runbook')
            self.assertIn('section_index', chunk.metadata)

    def test_chunking_is_bounded_repeatable_and_content_addressed(self):
        docs = load_runbooks()
        a = chunk_documents(docs, max_words=10)
        self.assertEqual(a, chunk_documents(docs, max_words=10))
        self.assertTrue(all(len(c.text.split()) <= 10 for c in a))
        self.assertEqual(len(a), len({c.chunk_id for c in a}))
        docs[0].sections[0] = 'Changed operational instruction'
        self.assertNotEqual(a[0].chunk_id, chunk_documents(docs, max_words=10)[0].chunk_id)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                chunk_documents(docs, invalid)
        with self.assertRaises(RetrievalError):
            chunk_documents(docs + docs)

    def test_corpus_excludes_scenarios_and_evaluation(self):
        original = Path.open
        def guard(path, *args, **kwargs):
            self.assertNotIn('evaluation', path.parts)
            self.assertNotIn('scenarios', path.parts)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guard):
            docs = load_runbooks()
        self.assertEqual([doc.document_id for doc in docs], ['rb-deployment-5xx'])
        corpus = ' '.join(c.model_dump_json() for c in chunk_documents(docs))
        for secret in ('SEV-2', 'dep-checkout-231', 'log-checkout-service-06'):
            self.assertNotIn(secret, corpus)

    def test_service_top_k_and_deterministic_ties(self):
        rows = self.retriever.search('test query', 'checkout-service', top_k=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.chunk_id for r in rows], sorted(c.chunk_id for c in self.chunks)[:2])
        self.assertTrue(all(r.score == 1.0 for r in rows))
        self.assertEqual(self.retriever.search('test', 'database'), [])
        self.assertEqual(self.retriever.search('test', 'unknown'), [])
        self.assertEqual(len(self.retriever.search('test', top_k=100)), 6)

    def test_empty_and_no_match(self):
        self.assertEqual(self.retriever.search('!!!'), [])
        self.store.vectors = [[0.0, 1.0] for _ in self.chunks]
        self.assertEqual(self.retriever.search('orthogonal query'), [])
        self.store.chunks = []
        self.assertEqual(self.retriever.search('empty corpus'), [])

    def test_invalid_request_fails_before_embedding(self):
        invalid = [dict(query=''), dict(query='  '), dict(query=1), dict(query='x', service=' '),
                   dict(query='x', service=1), dict(query='x', top_k=0), dict(query='x', top_k=-1),
                   dict(query='x', top_k=101), dict(query='x', top_k=True), dict(query='x', top_k=1.5)]
        with patch.object(self.embedder, 'embed_query', side_effect=AssertionError('Should not embed')):
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                    self.retriever.search(**kwargs)

    def test_invalid_embeddings_are_rejected_before_storage(self):
        for vector in ([0.0, 0.0], [1.0], [float('nan'), 1.0], [float('inf'), 0.0]):
            with self.subTest(vector=vector), self.assertRaises(RetrievalError):
                validate_vector(vector, 2)
        with patch.object(self.embedder, 'embed_documents', return_value=[]):
            writer = Mock()
            with self.assertRaises(RetrievalError):
                ingest_runbooks(writer, self.embedder)
            writer.replace.assert_not_called()

    def test_tool_preserves_legacy_fields_and_adds_chunk_fields(self):
        tools = ToolLayer(retriever=self.retriever)
        with patch.object(self.store, 'replace', side_effect=AssertionError('Search must not ingest')):
            rows = tools.search_runbooks('meaningful query', 'checkout-service', top_k=2)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row.runbook_id, 'rb-deployment-5xx')
            self.assertTrue(row.title and row.steps and row.tags and row.symptoms)
            self.assertTrue(row.chunk_id and row.text and row.metadata)
            self.assertEqual(row.score, 1.0)
            self.assertEqual(type(row).model_validate_json(row.model_dump_json()), row)

    def test_tool_does_not_fallback_on_semantic_failure(self):
        retriever = Mock()
        retriever.search.side_effect = RetrievalError('database unavailable')
        with self.assertRaises(RetrievalError):
            ToolLayer(retriever=retriever).search_runbooks('deployment')

    def test_tool_top_k_validation(self):
        for top_k in (0, -1, True, 101, '2'):
            with self.subTest(top_k=top_k), self.assertRaises(ValidationError):
                ToolLayer(retriever=self.retriever).search_runbooks('query', top_k=top_k)

    def test_score_threshold_validation(self):
        for score in (-2, 2, float('nan')):
            with self.subTest(score=score), self.assertRaises(ValueError):
                SemanticRetriever(self.embedder, self.store, score)

    def test_environment_configuration_routes_tool_to_semantic_retriever(self):
        with patch.dict('os.environ', {'SIGNAL_TRACE_RETRIEVAL_DATABASE_URL': 'postgresql://example/knowledge'}):
            with patch('signal_trace.retrieval.runtime.configured_retriever', return_value=self.retriever) as factory:
                rows = ToolLayer().search_runbooks('semantic question', top_k=1)
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0].score)
        factory.assert_called_once()

    def test_search_uses_no_ingestion_or_operational_file_reads(self):
        with patch.object(Path, 'open', side_effect=AssertionError('Search must use prepared corpus')):
            with patch.object(self.store, 'replace', side_effect=AssertionError('No writes')):
                self.assertTrue(ToolLayer(retriever=self.retriever).search_runbooks('query'))

    def test_runbook_steps_keep_context_and_complete_instructions(self):
        book = load_runbooks()[0].metadata['runbook']
        self.assertEqual(len(self.chunks), 6)
        for index, step in enumerate(book['steps'], 1):
            chunk = self.chunks[index]
            self.assertIn(step, chunk.text)
            self.assertTrue(all(symptom in chunk.text for symptom in book['symptoms']))
            self.assertEqual(chunk.metadata['section_id'], f'step:{index}')
            self.assertEqual(chunk.metadata['heading'], book['title'])
            self.assertEqual(chunk.services, book['services'])
            self.assertEqual(chunk.source, book['source'])
            self.assertFalse(chunk.metadata['hard_split'])
            self.assertEqual(chunk.metadata['part_count'], 1)
            self.assertTrue(chunk.text.startswith(book['title'] + '\n\n'))

    def test_sentence_boundaries_and_repeated_headings(self):
        first = 'Check downstream logs before restarting the process.'
        second = 'Compare dependency metrics before requesting an approved rollback.'
        document = Document(document_id='boundary-test', source='runbook:boundary-test',
                            services=['checkout-service'], title='Troubleshooting guide',
                            sections=[first + ' ' + second], section_ids=['step:1'])
        chunks = chunk_documents([document], max_words=12)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([chunk.text for chunk in chunks],
                         ['Troubleshooting guide\n\n' + first, 'Troubleshooting guide\n\n' + second])
        self.assertTrue(all(not c.metadata['hard_split'] for c in chunks))
        self.assertEqual([c.metadata['part_index'] for c in chunks], [0, 1])
        self.assertTrue(all(c.metadata['part_count'] == 2 for c in chunks))

    def test_oversized_sentence_has_no_lost_words_and_is_flagged(self):
        text = ' '.join(f'word{n}' for n in range(21))
        document = Document(document_id='long', source='runbook:long', services=['checkout-service'],
                            title='Long guide', sections=[text])
        chunks = chunk_documents([document], max_words=10)
        self.assertEqual(' '.join(c.text.split('\n\n', 1)[1] for c in chunks), text)
        self.assertTrue(all(c.metadata['hard_split'] for c in chunks))
        self.assertTrue(all(len(c.text.split()) <= 10 for c in chunks))
        self.assertTrue(all(c.text.startswith('Long guide\n\n') for c in chunks))

    def test_section_identifiers_are_consistent(self):
        for identifiers in (['one'], ['duplicate', 'duplicate']):
            with self.subTest(identifiers=identifiers), self.assertRaises(ValidationError):
                Document(document_id='id', source='runbook:id', services=['checkout-service'],
                         title='Title', sections=['first', 'second'], section_ids=identifiers)
