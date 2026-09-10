"""Measure labeled chunk retrieval. Reads evaluation labels; never ingests them.

Run from the repository root after installing the editable project and ingesting
runbooks into a disposable database. Uses SIGNAL_TRACE_TEST_DATABASE_URL.
"""
import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path

from signal_trace.retrieval.embeddings import FastEmbedProvider
from signal_trace.retrieval.service import SemanticRetriever
from signal_trace.retrieval.storage import PgVectorStore


def section_label(row):
    if 'section_id' in row.metadata:
        return row.metadata['section_id']
    # Original Part 4 chunking: two symptoms, then five steps.
    index = row.metadata['section_index']
    return 'symptoms' if index < 2 else f'step:{index - 1}'


def evaluate(cases, retriever, threshold):
    details = []
    for case in cases:
        # Retrieve raw top 3 for diagnosis; the live score threshold is applied below.
        rows = retriever.search(case['query'], service=case['service'], top_k=3)
        returned = [row for row in rows if row.score >= threshold]
        relevant = lambda row: (case['expected_runbook'] is not None
                               and row.document_id == case['expected_runbook']
                               and section_label(row) == case['expected_section'])
        rank = next((i for i, row in enumerate(returned, 1) if relevant(row)), None)
        details.append({**case, 'expected_rank_within_returned_top3': rank,
                        'returned_count': len(returned),
                        'top3': [{'chunk_id': row.chunk_id, 'document_id': row.document_id,
                                  'section': section_label(row), 'score': row.score,
                                  'returned_at_threshold': row.score >= threshold,
                                  'relevant': relevant(row), 'text': row.text,
                                  'source': row.source, 'services': row.services,
                                  'metadata': row.metadata} for row in rows]})
    positives = [row for row in details if row['expected_runbook'] is not None]
    negatives = [row for row in details if row['expected_runbook'] is None]
    ranks = [row['expected_rank_within_returned_top3'] for row in positives]
    # One judged relevant idea per query: recall equals hit rate. MRR is truncated
    # at 3: missing relevant ideas contribute zero, not a guessed lower rank.
    return {'threshold': threshold, 'positive_queries': len(positives),
            'negative_queries': len(negatives),
            'recall_at_1': sum(rank == 1 for rank in ranks) / len(positives),
            'recall_at_3': sum(rank is not None for rank in ranks) / len(positives),
            'mrr_at_3': sum(1 / rank if rank else 0 for rank in ranks) / len(positives),
            'negative_queries_with_results': sum(row['returned_count'] > 0 for row in negatives),
            'cases': details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threshold', type=float, default=.55)
    args = parser.parse_args()
    database_url = os.environ.get('SIGNAL_TRACE_TEST_DATABASE_URL')
    if not database_url:
        parser.error('Set SIGNAL_TRACE_TEST_DATABASE_URL to an ingested test database')
    cases = json.loads(Path('evaluation/retrieval/runbook_queries.json').read_text())['cases']
    provider = FastEmbedProvider(cache_dir=Path('.cache/embeddings'))
    retriever = SemanticRetriever(provider, PgVectorStore(database_url), min_score=-1.0)
    report = evaluate(cases, retriever, args.threshold)
    report['embedding_model'] = provider.model_id
    report['fastembed_version'] = version('fastembed')
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, indent=2))
    for case in report['cases']:
        print(case['id'], 'expected=' + str(case['expected_section']),
              'rank=' + str(case['expected_rank_within_returned_top3']),
              [(row['section'], round(row['score'], 4), row['returned_at_threshold']) for row in case['top3']])


if __name__ == '__main__':
    main()
