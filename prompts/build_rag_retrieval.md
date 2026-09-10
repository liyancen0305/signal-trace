# Part 4: RAG / Semantic Retrieval

Preserve Parts 1–3 and add semantic retrieval for operational runbooks and
troubleshooting knowledge. Implement document parsing → deterministic chunking →
replaceable embeddings → PostgreSQL/pgvector chunk storage → similarity search →
normalized relevant chunks. Never include evaluation ground truth in the corpus.

Keep ingestion, embeddings, storage, retrieval, and tool contracts separate.
Store chunk ID, document/source ID, applicable service(s), text, embedding, and
metadata. Hide PostgreSQL details behind an interface. Isolate provider-specific
embedding logic so another provider can replace it later.

Support `query: str`, optional `service: str`, and `top_k: int`. Return relevant
text, source/runbook, service(s), similarity score, and metadata. Connect
`search_runbooks()` to retrieval while preserving its existing response fields
and read-only behavior. Ingestion is an explicit operation, never a query side
effect. Do not silently fall back if configured semantic retrieval fails.

Test relevance, semantic matching without exact keyword overlap, service filters,
top-k, empty/no-match behavior, metadata/source accuracy, corpus isolation, and
all existing Parts 1–3 behavior. Use deterministic doubles for offline tests and
real PostgreSQL/pgvector plus an embedding model for integration/smoke tests.

Update README with architecture and setup. Report created/changed files, the
full test result, representative results for two queries, assumptions, and any
remaining concerns. A small number of additional synthetic knowledge documents
is allowed only if needed for relevance tests; do not add incident use cases.

Do not implement orchestration, LLM reasoning, hypothesis generation, final
triage, production writes, or Part 5. Do not push to GitHub without approval.
