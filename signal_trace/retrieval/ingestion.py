"""Explicit operational-runbook ingestion; never traverses evaluation or scenarios."""
from hashlib import sha256
from pathlib import Path
import re

from signal_trace.retrieval.embeddings import EmbeddingProvider, validate_vector
from signal_trace.retrieval.models import Chunk, Document, RetrievalError
from signal_trace.retrieval.storage import VectorWriter


def load_runbooks(root: Path | None = None) -> list[Document]:
    # Reuse the validated Part 3 adapter, exclusively its operational runbooks.
    from signal_trace.tools.source import SyntheticSource
    documents = []
    for book in SyntheticSource(root).runbooks():
        context = 'Symptoms: ' + '; '.join(book.symptoms) + '.'
        documents.append(Document(
            document_id=book.runbook_id, source=book.source, services=book.services,
            title=book.title,
            sections=[context] + [f'Step {index}: {step}\n\n{context}'
                                  for index, step in enumerate(book.steps, 1)],
            section_ids=['symptoms'] + [f'step:{index}' for index in range(1, len(book.steps) + 1)],
            metadata={'kind': 'runbook', 'runbook': book.model_dump(mode='json')},
        ))
    return documents


def _split_section(section: str, budget: int) -> list[tuple[str, bool]]:
    """Keep sentences intact when they fit; flag unavoidable oversized sentences.

    Sentence detection is intentionally conservative (terminal punctuation followed
    by whitespace), not a general linguistic parser. No section is merged with another.
    """
    units = re.split(r'(?<=[.!?])\s+', section.strip())
    parts: list[tuple[str, bool]] = []
    pending: list[str] = []
    count = 0
    for sentence in units:
        words = sentence.split()
        if count + len(words) > budget and pending:
            parts.append(('\n\n'.join(pending), False))
            pending, count = [], 0
        if len(words) > budget:
            # An indivisible sentence exceeding the hard limit must be fragmented.
            # The title is repeated by the caller and metadata marks this explicitly.
            parts.extend((' '.join(words[offset:offset + budget]), True)
                         for offset in range(0, len(words), budget))
        else:
            pending.append(sentence)
            count += len(words)
    if pending:
        parts.append(('\n\n'.join(pending), False))
    return parts


def chunk_documents(documents: list[Document], max_words: int = 100) -> list[Chunk]:
    if isinstance(max_words, bool) or not isinstance(max_words, int) or max_words < 1:
        raise ValueError('max_words must be a positive integer')
    ids = [document.document_id for document in documents]
    if len(set(ids)) != len(ids):
        raise RetrievalError('Duplicate document IDs')
    chunks = []
    for document in sorted(documents, key=lambda d: d.document_id):
        budget = max_words - len(document.title.split())
        if budget < 1:
            raise ValueError('max_words must leave room for content after the document heading')
        for section_index, section in enumerate(document.sections):
            section_id = (document.section_ids[section_index] if document.section_ids
                          else f'section:{section_index}')
            parts = _split_section(section, budget)
            word_offset = 0
            for part_index, (body, hard_split) in enumerate(parts):
                text = f'{document.title}\n\n{body}'
                identity = f'{document.document_id}\0{section_id}\0{part_index}\0{text}'
                chunks.append(Chunk(
                    chunk_id=sha256(identity.encode()).hexdigest(), document_id=document.document_id,
                    source=document.source, services=document.services, text=text,
                    metadata={**document.metadata, 'section_index': section_index,
                              'section_id': section_id, 'heading': document.title,
                              'word_offset': word_offset, 'part_index': part_index,
                              'part_count': len(parts), 'hard_split': hard_split},
                ))
                word_offset += len(body.split())
    return chunks


def ingest_runbooks(writer: VectorWriter, embedder: EmbeddingProvider, root: Path | None = None) -> int:
    chunks = chunk_documents(load_runbooks(root))
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    if len(vectors) != len(chunks):
        raise RetrievalError('Embedding count does not match chunk count')
    for vector in vectors:
        validate_vector(vector, embedder.dimensions)
    # The full snapshot is prepared before the atomic replacement begins.
    writer.replace(chunks, vectors, embedder.model_id, embedder.dimensions)
    return len(chunks)
