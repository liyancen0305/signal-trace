"""Explicit local corpus ingestion and retrieval smoke-test CLI."""
import argparse
import json
from pathlib import Path

from signal_trace.config import Settings
from signal_trace.retrieval.embeddings import FastEmbedProvider
from signal_trace.retrieval.ingestion import ingest_runbooks
from signal_trace.retrieval.models import RetrievalError
from signal_trace.retrieval.service import SemanticRetriever
from signal_trace.retrieval.storage import PgVectorStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    ingest = commands.add_parser('ingest')
    ingest.add_argument('--root', type=Path, default=None)
    search = commands.add_parser('search')
    search.add_argument('query')
    search.add_argument('--service')
    search.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    settings = Settings()
    if settings.retrieval_database_url is None:
        parser.error('Set SIGNAL_TRACE_RETRIEVAL_DATABASE_URL')
    store = PgVectorStore(settings.retrieval_database_url.get_secret_value())
    try:
        embedder = FastEmbedProvider(settings.embedding_model, settings.embedding_cache_dir)
        if args.command == 'ingest':
            store.initialize()
            print(json.dumps({'chunks_ingested': ingest_runbooks(store, embedder, args.root)}))
        else:
            results = SemanticRetriever(embedder, store, settings.retrieval_min_score).search(
                args.query, args.service, args.top_k)
            print(json.dumps([result.model_dump(mode='json') for result in results], indent=2))
    except RetrievalError as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
