# Signal Trace Part 5 final validation — 2026-09-15

## Outcome

**Partially validated: semantic RAG and all regressions pass; the live LLM smoke test is blocked.** The completed investigation used the existing offline reference provider. It must not be presented as proof of live LLM tool selection, hypothesis revision, or stopping behavior.

- Real semantic RAG: **yes**, FastEmbed BAAI/bge-small-en-v1.5 (384 dimensions), PostgreSQL 16.15, pgvector 0.6.0.
- Live generative LLM: **no**. No generative request, live tool-call trajectory, or live final triage exists.
- Full Parts 1–5 regression: **99 passed, 0 failed, 0 skipped** (7.438 seconds).
- No Agent features or Part 6 were added. No production code or Tool Layer contracts changed. No application bugs were fixed. No commit or push was made.

## 1. Semantic RAG: actual execution

The existing `tests/test_agent_semantic.py::semantic_smoke` ran UC1 through `configured_investigator`, ToolLayer.search_runbooks, SemanticRetriever, FastEmbedProvider.embed_query, and PgVectorStore.search. Instrumentation wrapped the real implementations. It blocked keyword fallback, raw runbook/evaluation reads during investigation, and in-investigation corpus writes.

A separate disposable smoke database was initialized and ingested before investigation. The test suite used another disposable database. The six-chunk corpus was embedded using the cached real model. Database retrieval ran in read-only transactions and recorded the actual cosine-distance SQL:

```sql
SELECT chunk_id, document_id, source, services,
                    content AS text, metadata, 1 - (embedding <=> %s::vector) AS score
                    FROM signal_trace_knowledge_chunks
                    WHERE (%s::text IS NULL OR %s = ANY(services))
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector, chunk_id
                    LIMIT %s
```

Actual query: **`5xx`**; service: **`checkout-service`**; requested Top-K: **5**; minimum cosine score: **0.55**. One query embedding and one vector search executed; five rows returned. These are cosine similarities, not relevance probabilities.

All five results cite `runbook:rb-deployment-5xx` (document `rb-deployment-5xx`). Full text, IDs, scores, metadata, and SQL are in [semantic path audit](uc1_agent_semantic_audit.json).

| Rank | Section | Cosine score | Evidence added to AgentState |
| ---: | --- | ---: | --- |
| 1 | symptoms | 0.734970093 | `ev-743f44faef06684777fe` |
| 2 | step:5 | 0.708200216 | `ev-13b04eee6b01f0cfb640` |
| 3 | step:1 | 0.707521779 | `ev-4af1f06b11fd003eda50` |
| 4 | step:4 | 0.678808253 | `ev-40c0a51ce0c9156bd0d8` |
| 5 | step:3 | 0.673043847 | `ev-24de01896a0462045e83` |

### Retrieved chunk 1

Chunk/source ID: `e369c79932bac8fa9ef8214873183591598fe34acc690b39f0c013a6e89ae4c5`

```text
Investigate elevated 5xx after deployment

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 2

Chunk/source ID: `e3285249827b46736b087a8a1a7085a9caa664f794dc13bcafe9a1e09f54dc02`

```text
Investigate elevated 5xx after deployment

Step 5: After mitigation, verify recovery across several metric windows.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 3

Chunk/source ID: `a5fcc5618ff56493900ec96a965080a5d99b65020354e836c9bf465160dcb6bd`

```text
Investigate elevated 5xx after deployment

Step 1: Compare error rate before and after recent deployments.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 4

Chunk/source ID: `8de95f12c078c9bba41a8331b787fbf9d9c14000ba7c4d57a11dd81adb9ae5d6`

```text
Investigate elevated 5xx after deployment

Step 4: If evidence supports a regression, propose rollback to the last healthy version through the normal approval process.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

### Retrieved chunk 5

Chunk/source ID: `85c4aaa6faf0a02e410b18e0f42cf7f21accdc7832a5ba96104ed972e248be22`

```text
Investigate elevated 5xx after deployment

Step 3: Check dependency metrics and logs to distinguish local and downstream failures.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

For every chunk, `Evidence.source_id` equals its chunk ID; `tool` is `search_runbooks`; `call_ids` contains `call-9`. Text, score, source, and metadata exactly match the semantic response. All five enter AgentState and the final supporting evidence. Keyword fallback was blocked and never called.

## 2. Live LLM: exact blocker

The actual Settings configuration is `agent_provider=offline`, `agent_model=None`, `agent_base_url=http://localhost:11434`. There are no SIGNAL_TRACE model environment overrides in this session and no Ollama executable on PATH.

Attempting to construct the existing live provider through `configured_investigator` with provider `ollama` and the configured model produced:

```text
ValueError: An explicit Ollama model name is required
```

An independent GET of `http://localhost:11434/api/tags` produced:

```text
URLError: <urlopen error [Errno 111] Connection refused>
```

[Exact live preflight failures and tracebacks](part5_live_smoke.json). Generation requests: **0**. Live tool calls: **0**. Live final triage: **null**. This is a configuration/availability failure before a live investigation, not an LLM reasoning failure. The offline semantic run is reported separately; no failed live call fell back to offline.

A responding Ollama server and explicit installed model name are required to finish this item through the current provider abstraction. Model choice and tool sequence were not invented for this validation. The user was asked for the missing configuration.

## 3. Actual completed investigation trajectory (offline reference provider)

UC1 incident → empty AgentState → assessment → provider-selected tool → Tool Layer records → evidence/provenance in AgentState → reassessment → sufficiency/iteration check → structured result. This executed the existing architecture, but the decisions below are deterministic reference-policy decisions, not live LLM decisions.

| Call | Tool | Service | Records returned | Decision rationale |
| --- | --- | --- | ---: | --- |
| call-1 | get_metrics | checkout-service | 15 | Measure impact and baseline |
| call-2 | search_logs | checkout-service | 15 | Inspect error signatures across baseline and incident |
| call-3 | get_recent_deployments | checkout-service | 1 | Check changes as a possible trigger, not proof of cause |
| call-4 | get_dependencies | checkout-service | 1 | Identify competing downstream explanations |
| call-5 | get_metrics | database | 0 | Compare downstream error rates |
| call-6 | search_logs | database | 15 | No HTTP metric series; inspect dependency health logs |
| call-7 | get_metrics | inventory-service | 15 | Compare downstream error rates |
| call-8 | get_metrics | payment-service | 15 | Compare downstream error rates |
| call-9 | search_runbooks | checkout-service | 5 | Retrieve investigation and mitigation guidance |

The database HTTP metric call returned no records. The next call obtained actual database health logs; absence of HTTP metrics was not treated as evidence of health. All arguments, returned records, and evidence IDs are retained in [full investigation state](uc1_agent_semantic_run.json).

| Iteration | Total evidence | Primary hypothesis | Local confidence | Dependency confidence | Deployment confidence |
| ---: | ---: | --- | ---: | ---: | ---: |
| 0 | 0 | None | — | — | — |
| 1 | 15 | dependency-failure | 0.3 | 0.35 | — |
| 2 | 30 | local-failure | 0.55 | 0.35 | — |
| 3 | 31 | local-failure | 0.55 | 0.35 | 0.55 |
| 4 | 32 | local-failure | 0.55 | 0.35 | 0.55 |
| 5 | 32 | local-failure | 0.55 | 0.35 | 0.55 |
| 6 | 47 | local-failure | 0.55 | 0.35 | 0.55 |
| 7 | 62 | local-failure | 0.55 | 0.35 | 0.55 |
| 8 | 77 | deployment-regression | 0.55 | 0.15 | 0.88 |
| 9 | 82 | deployment-regression | 0.55 | 0.15 | 0.88 |

Deployment confidence first appears at 0.55 after deployment retrieval and remains there until dependency observations are complete. It rises to 0.88 at call-8. Call-9 supplies investigation guidance without changing causal confidence. Stopping reason: **`sufficient_evidence`**, after **9 of 12** allowed iterations. The full suite also verifies the iteration-limit path and incomplete structured triage.

## 4. Final structured triage and post-run comparison

[Complete schema-validated structured result](uc1_agent_semantic_triage.json), including 25 exact supporting Evidence objects. Compact projection:

```json
{
  "incident_id": "uc1-deployment-5xx",
  "affected_service": "checkout-service",
  "affected_services": [
    "checkout-service"
  ],
  "root_cause": "Likely deployment regression in checkout-service after version 2.3.1. Observed error: Checkout failed at CheckoutHandler.applyDiscount: discount.code is null",
  "severity": "SEV-2",
  "severity_rationale": "Provisional local policy: sustained 5xx above the alert threshold is SEV-2; not an organization-wide severity classification.",
  "confidence": 0.88,
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

Alternatives retained: local application failure with unestablished trigger (0.55), and possible downstream propagation (0.15) with contradicting health evidence.

The post-run audit read `evaluation/uc1_deployment_5xx.json` only after investigation completed. The user’s expected version is checked only by evaluator code. Neither was supplied as model context.

| Dimension | Expected | Actual | Result |
| --- | --- | --- | --- |
| Affected service | checkout-service | checkout-service | Match |
| Root cause | Deployment regression | Likely deployment regression with observed discount null-handling error | Match as a likely triage diagnosis |
| Deployment/version | checkout-service v2.3.1 | dep-checkout-231 / 2.3.1 from deployment tool | Match |
| Severity | SEV-2 | SEV-2 | Match |

## 5. Grounding checks

[Assertion-backed grounding audit and comparison](part5_grounding_audit.json); [reproducible post-run auditor](../scripts/audit_part5_validation.py).

- **82 evidence records**: checked content hashes, exact tool payloads, provenance, and equality to original operational records or captured semantic responses.
- **90 historical citation references**: every citation existed at the iteration when used; no future evidence was cited.
- **25 final supporting records**: exactly equal to AgentState evidence. Pydantic and generated JSON Schema validation passed.
- **Ground-truth isolation**: evaluation and raw-runbook file reads were blocked throughout the semantic investigation; the existing isolation tests passed. Labels were read by the post-run evaluator only.
- **Important conclusions**: the claim-to-evidence map below covers the observed service impact, version/change, error signature, sampled dependency health, severity, and recommended guidance. Manual inspection found no invented operational fact in this completed reference run.
- **Timing alone**: the diagnosis combines baseline 0.1% and incident 18% 5xx measurements, a new local error signature, deployment 2.3.1, and dependency observations. The existing counterfactual test removes application ERROR logs and verifies that timing alone does not produce a sufficient deployment diagnosis. That test passed.

| Claim | Supporting evidence IDs |
| --- | --- |
| deployment and version | `ev-e71a2587d95b5223c282` |
| baseline and elevated 5xx | `ev-0c03e7177027ed999698`, `ev-75c083c020b44b1c8a1e` |
| new local error signature | `ev-220d0c7cbb55febe1cb2` |
| sampled dependency health | `ev-1cfeeee50d990ecc1df1`, `ev-3649651699a414ef840a`, `ev-784b29729efe1c3005f6` |
| provisional severity | `ev-75c083c020b44b1c8a1e`, `ev-f575a75c1392bfd9bad1`, `ev-c44573d83fe34fe68e37`, `ev-63bc0e2ee1f284aef1a8`, `ev-8e5b81c6a9635b48bddc`, `ev-71b45f1d729ffbbedc60`, `ev-20f9d116aee3a8c3763b`, `ev-ec314a89a4884d9c244e`, `ev-24fa076a19fef4dc26cf` |
| investigation and human approved mitigation guidance | `ev-743f44faef06684777fe`, `ev-13b04eee6b01f0cfb640`, `ev-4af1f06b11fd003eda50`, `ev-40c0a51ce0c9156bd0d8`, `ev-24de01896a0462045e83` |

There are nine complete consecutive elevated 60-second windows, exceeding the incident requirement of three. SEV-2 is the existing provisional policy. Runbooks support investigation/mitigation recommendations; they are not operational proof. A likely deployment diagnosis does not establish the exact code mechanism. No rollback, recovery outcome, code diff, or reproduction was observed or claimed.

Citation/payload checks are mechanical; they do not prove arbitrary natural-language entailment. Live LLM grounding remains untested. The generic runtime rejects unknown citations and rewritten records, but does not independently prove that every model-written sentence follows from those records; it also relies on the provider’s sufficiency assessment.

## 6. Complete regression and retrieval evaluation

[Full unittest log](part5_regression.log): **99 passed, 0 failed, 0 skipped**.

| Test module | Passed |
| --- | ---: |
| test_agent | 21 |
| test_agent_semantic | 1 |
| test_backend | 11 |
| test_environment | 8 |
| test_retrieval | 17 |
| test_retrieval_evaluation | 2 |
| test_retrieval_postgres | 8 |
| test_tool_contracts | 14 |
| test_tools | 17 |

This includes Parts 1–4, Agent state/counterfactual behavior, unchanged Tool Layer contracts, real PostgreSQL/embedding integration, ground-truth isolation, transport contract checks, and structured output validation. The Ollama transport unit test uses a mock and is not counted as live LLM evidence. Synthetic environment schema/consistency validation also passed. The existing Starlette/httpx deprecation warning is non-failing.

[Fresh real retrieval evaluation](part5_retrieval_evaluation.json) exactly matches the Part 4 report:

- Recall@1: **0.20**; Recall@3: **1.00**; MRR@3: **0.566667** across 10 positive judgments.
- **1 of 5 negative queries still returns unsupported results**: unfiltered database connection exhaustion. This is a known quality limitation, not a new regression. The evaluation is not an all-pass relevance benchmark.

## 7. Changes, reproduction, and remaining limits

Only validation artifacts and `scripts/audit_part5_validation.py` were added/refreshed. The script audits a completed saved run and compares evaluation labels afterward; it does not run an Agent, change tools, prescribe a tool sequence, or implement Part 6. No application bugs were found or fixed. The missing temporary database was restored for validation and stopped afterward. The fresh semantic run, triage, and path-audit JSON were byte-identical to the prior artifacts. `git diff --check` and validation-artifact whitespace checks passed.

Full regression command (requires the disposable PostgreSQL/pgvector server):

```bash
SIGNAL_TRACE_TEST_DATABASE_URL='postgresql:///signal_trace_part5_test?host=/tmp/signal-trace-pg-socket&port=55432' \
SIGNAL_TRACE_TEST_REAL_EMBEDDINGS=1 \
.venv/bin/python -m unittest discover -s tests -v > reports/part5_regression.log 2>&1
```

The semantic artifacts were generated with the existing `semantic_smoke` helper using the separate `signal_trace_part5_smoke` database; its assertions enforce real embeddings/SQL and block fallback. Ingestion is setup before the run. After that run:

```bash
SIGNAL_TRACE_TEST_DATABASE_URL='postgresql:///signal_trace_part5_smoke?host=/tmp/signal-trace-pg-socket&port=55432' \
.venv/bin/python scripts/evaluate_retrieval.py --output reports/part5_retrieval_evaluation.json
.venv/bin/python scripts/audit_part5_validation.py
.venv/bin/python -m signal_trace.validation
```

Remaining Part 5 limitations:

1. Live LLM end-to-end reasoning remains **blocked/unvalidated** until an Ollama model and responding endpoint are available. No claim of full Part 5 acceptance is made.
2. One runbook/six chunks and one synthetic incident cannot establish broad retrieval or diagnosis quality. The 15-query retrieval evaluation is a development set.
3. Confidence 0.88 and severity are reference-policy heuristics. Correlation plus corroborating evidence supports triage, not a reproduced causal proof.
4. Final dependency citations are representative samples; the complete sampled interval is retained in AgentState. Observed health does not exclude every possible dependency failure.
5. The runtime enforces citation integrity and bounded iteration, but free-text grounding and sufficient-evidence quality remain provider responsibilities.
6. No operational mitigation was executed. No Part 6, commit, or push.
