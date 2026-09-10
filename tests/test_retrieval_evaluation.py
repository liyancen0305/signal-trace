"""Metric correctness independent of the learned embedding model."""
from pathlib import Path
import json
import unittest

from scripts.evaluate_retrieval import evaluate
from signal_trace.retrieval.models import SearchResult


def hit(section, score=.8):
    return SearchResult(chunk_id=section, document_id='book', source='runbook:book',
                        services=['service'], text='text', score=score,
                        metadata={'section_id': section})


class RetrievalEvaluationTests(unittest.TestCase):
    def test_metrics_count_misses_and_exclude_negative_queries(self):
        cases = [dict(id=str(i), query=str(i), service=None, expected_runbook='book',
                      expected_section='target') for i in range(3)]
        cases.append(dict(id='negative', query='negative', service=None,
                          expected_runbook=None, expected_section=None))
        class RankedResults:
            def search(self, query, service=None, top_k=3):
                return {'0': [hit('target')], '1': [hit('other'), hit('target', .7)],
                        '2': [hit('target', .5)], 'negative': [hit('other')]}[query]
        result = evaluate(cases, RankedResults(), .55)
        self.assertAlmostEqual(result['recall_at_1'], 1/3)
        self.assertAlmostEqual(result['recall_at_3'], 2/3)
        self.assertAlmostEqual(result['mrr_at_3'], .5)
        self.assertEqual(result['negative_queries_with_results'], 1)
        self.assertFalse(result['cases'][2]['top3'][0]['returned_at_threshold'])

    def test_fixed_judgments_are_separate_from_corpus(self):
        root = Path(__file__).resolve().parents[1]
        cases = json.loads((root / 'evaluation/retrieval/runbook_queries.json').read_text())['cases']
        self.assertEqual(len(cases), 15)
        self.assertEqual(len({c['id'] for c in cases}), 15)
        self.assertEqual(sum(c['expected_runbook'] is not None for c in cases), 10)
        self.assertEqual(sum(c['expected_runbook'] is None for c in cases), 5)
