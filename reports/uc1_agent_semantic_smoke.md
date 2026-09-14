# Part 5 semantic RAG smoke test

**Passed:** real PostgreSQL/pgvector semantic retrieval was used during a complete UC1 Agent investigation. Keyword fallback was blocked and never called. The reasoning provider was the offline reference model; this was not a live generative LLM run.

## Configuration and verification

PostgreSQL 16 on an isolated Unix socket under `/tmp`; pgvector 0.6.0. Explicit setup ingested 6 chunks from the existing runbook corpus using `fastembed:BAAI/bge-small-en-v1.5` (384 dimensions). `SIGNAL_TRACE_RETRIEVAL_DATABASE_URL` selected the existing Part 4 configured retrieval path. Threshold: 0.55; top_k: 5; service filter: checkout-service.

The smoke test delegates to the real SemanticRetriever, FastEmbedProvider.embed_query, PgVectorStore.search, psycopg connection, and SQL execution. Captured SQL includes `1 - (embedding <=> %s::vector)` and cosine-distance ordering. Connections were verified read-only. The Agent called search_runbooks through the Tool Layer; database calls originated only in retrieval/storage.py. Raw runbook and evaluation file reads were blocked during investigation; SyntheticSource.runbooks was blocked to detect any keyword fallback. Ingestion ran separately before investigation.

[Exact retrieval path audit, SQL, and ranked chunk payloads](uc1_agent_semantic_audit.json). No configuration/environment or application logic failure occurred; no application bugs were fixed.

## Initial incident

```json
{
  "schema_version": "1.0",
  "incident_id": "uc1-deployment-5xx",
  "title": "Checkout HTTP 5xx alert",
  "observation_start": "2026-01-15T10:00:00Z",
  "observation_end": "2026-01-15T10:15:00Z",
  "alert": {
    "timestamp": "2026-01-15T10:09:00Z",
    "service_id": "checkout-service",
    "metric_name": "http_requests",
    "condition": "error_5xx_rate > threshold for consecutive windows",
    "threshold": 0.05,
    "consecutive_windows": 3
  }
}
```

## Tool-call sequence

| Step | Tool | Service | Records |
| --- | --- | --- | ---: |
| call-1 | get_metrics | checkout-service | 15 |
| call-2 | search_logs | checkout-service | 15 |
| call-3 | get_recent_deployments | checkout-service | 1 |
| call-4 | get_dependencies | checkout-service | 1 |
| call-5 | get_metrics | database | 0 |
| call-6 | search_logs | database | 15 |
| call-7 | get_metrics | inventory-service | 15 |
| call-8 | get_metrics | payment-service | 15 |
| call-9 | search_runbooks | checkout-service | 5 |

## Semantic retrieval results

Actual Agent query: `5xx`. All results come from `runbook:rb-deployment-5xx` (Investigate elevated 5xx after deployment). Each applies to checkout-service, payment-service, and inventory-service; the query was filtered to checkout-service.

| Rank | Chunk section | Cosine similarity | Chunk ID |
| ---: | --- | ---: | --- |
| 1 | symptoms | 0.734970 | e369c79932bac8fa9ef8214873183591598fe34acc690b39f0c013a6e89ae4c5 |
| 2 | step:5 | 0.708200 | e3285249827b46736b087a8a1a7085a9caa664f794dc13bcafe9a1e09f54dc02 |
| 3 | step:1 | 0.707522 | a5fcc5618ff56493900ec96a965080a5d99b65020354e836c9bf465160dcb6bd |
| 4 | step:4 | 0.678808 | 8de95f12c078c9bba41a8331b787fbf9d9c14000ba7c4d57a11dd81adb9ae5d6 |
| 5 | step:3 | 0.673044 | 85c4aaa6faf0a02e410b18e0f42cf7f21accdc7832a5ba96104ed972e248be22 |

### Retrieved chunk 1

```text
Investigate elevated 5xx after deployment

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 2

```text
Investigate elevated 5xx after deployment

Step 5: After mitigation, verify recovery across several metric windows.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 3

```text
Investigate elevated 5xx after deployment

Step 1: Compare error rate before and after recent deployments.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 4

```text
Investigate elevated 5xx after deployment

Step 4: If evidence supports a regression, propose rollback to the last healthy version through the normal approval process.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 5

```text
Investigate elevated 5xx after deployment

Step 3: Check dependency metrics and logs to distinguish local and downstream failures.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

## Evidence and hypothesis updates

The Agent retained 82 evidence items, including five distinct semantic chunks from call-9. Each chunk's evidence.source_id equals its chunk_id; record text, score, source, metadata, and call provenance match the actual retrieval response. All five also appear in the final structured result's supporting_evidence. These checks ran as assertions, not just output inspection.

Operational observations remain checkout 5xx rising from 0.1% to 18%, the v2.3.1 deployment, discount null-handling errors, and healthy sampled dependencies. Runbooks are guidance rather than proof of deployment causation.

| Iteration | Primary | Deployment confidence |
| ---: | --- | ---: |
| 0 | None | — |
| 1 | dependency-failure | — |
| 2 | local-failure | — |
| 3 | local-failure | 0.55 |
| 4 | local-failure | 0.55 |
| 5 | local-failure | 0.55 |
| 6 | local-failure | 0.55 |
| 7 | local-failure | 0.55 |
| 8 | deployment-regression | 0.88 |
| 9 | deployment-regression | 0.88 |

Stopped with `sufficient_evidence` after 9/12 calls. Deployment confidence stayed at 0.55 until dependency checks, then rose to 0.88. Retrieving runbooks did not change causal confidence.

## Final structured triage

[Complete validated triage JSON](uc1_agent_semantic_triage.json), including exact supporting records and alternative-hypothesis citations. [Full investigation state and trace](uc1_agent_semantic_run.json). Compact projection:

```json
{
  "incident_id": "uc1-deployment-5xx",
  "severity": "SEV-2",
  "affected_services": [
    "checkout-service"
  ],
  "primary_hypothesis": "Likely deployment regression in checkout-service after version 2.3.1. Observed error: Checkout failed at CheckoutHandler.applyDiscount: discount.code is null",
  "confidence": 0.88,
  "alternative_hypotheses": [
    {
      "hypothesis": "Local application failure in checkout-service; trigger not established.",
      "confidence": 0.55
    },
    {
      "hypothesis": "A downstream failure may be propagating to the alerted service.",
      "confidence": 0.15
    }
  ],
  "missing_information": [
    "Code/configuration diff and reproduction to confirm the failure mechanism"
  ],
  "recommended_actions": [
    "Propose rollback to the last verified healthy version through the normal human approval process.",
    "Inspect code/configuration changes and reproduce the observed error.",
    "Verify error-rate recovery across several complete metric windows after any approved mitigation."
  ]
}
```

The observed diagnosis matches the user-specified UC1 expectations: checkout-service, deployment regression, SEV-2. Evaluation ground truth was never loaded into the Agent.

## Regression test and limits

Added one focused test in `tests/test_agent_semantic.py`: the existing tests covered real semantic retrieval and Agent orchestration separately, but did not verify their configuration-driven connection with real embeddings and SQL. It ingests a disposable corpus, runs UC1, checks the real call path, blocks fallback, verifies provenance, and checks the expected diagnosis. This is an integration test, not a new evaluation harness.

Full Parts 1–5 suite: **99 passed, 0 failed, 0 skipped**, with PostgreSQL and real embeddings enabled. `git diff --check` passed. No production code changed for this smoke test.

```bash
SIGNAL_TRACE_TEST_DATABASE_URL='postgresql:///signal_trace_part5_test?host=/tmp/signal-trace-pg-socket&port=55432' \
SIGNAL_TRACE_TEST_REAL_EMBEDDINGS=1 \
.venv/bin/python -m unittest discover -s tests -v
```

For the focused test alone, add `-p test_agent_semantic.py`. Use only a disposable database: setup replaces its knowledge corpus. The temporary test server was stopped after verification.

The corpus contains only one runbook split into six chunks; the broad query `5xx` does not establish general retrieval quality. Cosine similarities are not calibrated relevance probabilities or causal confidence. The deterministic Agent preserves retrieved guidance but does not exercise live LLM interpretation of that guidance. Live LLM reasoning/tool-selection quality remains unverified. SEV-2 and 0.88 confidence remain reference-policy heuristics; code diff/reproduction is still needed. No Part 6, new incident use cases, commits, or pushes were added.
