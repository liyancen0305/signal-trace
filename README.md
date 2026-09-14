# Signal Trace

Signal Trace is an evidence-grounded AI incident investigation agent project. Part 1 provides its initial synthetic production environment: static, deterministic evidence for **Use Case 1: deployment-related 5xx spike**. Part 2 adds a FastAPI backend that validates and acknowledges incident requests. Part 3 adds read-only tools over the existing operational fixtures. Part 4 adds local embeddings and PostgreSQL/pgvector retrieval of operational runbook chunks. Part 5 adds a typed investigation loop, an offline reference model, and an optional generative-model provider. Use Case 1 remains the only implemented scenario; there are no running microservices or remediation operations.

## Architecture

```text
.
├── environment/
│   ├── services/
│   │   ├── checkout-service.json
│   │   ├── payment-service.json
│   │   ├── inventory-service.json
│   │   └── database.json
│   └── topology.json
├── scenarios/uc1_deployment_5xx/
│   ├── incident.json
│   ├── logs.jsonl
│   ├── metrics.jsonl
│   └── deployments.jsonl
├── runbooks/deployment_5xx.json
├── evaluation/uc1_deployment_5xx.json
├── schemas/
│   ├── service.schema.json
│   ├── topology.schema.json
│   ├── log.schema.json
│   ├── metric.schema.json
│   ├── deployment.schema.json
│   ├── runbook.schema.json
│   ├── incident.schema.json
│   └── ground_truth.schema.json
├── signal_trace/
│   ├── __init__.py
│   ├── validation.py
│   ├── main.py              # compatibility entry point
│   ├── config.py
│   ├── api/                 # app.py entry point, health and incident routes
│   ├── models/              # request, acknowledgement, future triage contracts
│   ├── tools/               # typed contracts, queries, synthetic source adapter
│   ├── retrieval/           # ingestion, embeddings, pgvector, semantic search
│   └── agent/               # typed state, model providers, bounded investigation loop
├── tests/
│   ├── test_environment.py
│   ├── test_backend.py
│   ├── test_tools.py
│   ├── test_tool_contracts.py
│   ├── test_retrieval.py
│   └── test_retrieval_postgres.py
├── prompts/
│   ├── build_synthetic_environment.md
│   ├── build_backend_skeleton.md
│   ├── build_tool_layer.md
│   └── build_rag_retrieval.md
├── compose.yaml
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

Service definitions and topology describe the shared environment. Each scenario owns its observation window, alert, logs, metrics, and deployment history. Runbooks contain reusable investigation guidance. Evaluation answers are isolated under `evaluation/`; future agent evidence tools should read only `environment/`, `scenarios/`, and `runbooks/` and never expose evaluation answers. The validator intentionally reads both evidence and evaluation data.

Each JSON object or JSONL record uses `schema_version: "1.0"`. Strict JSON Schema contracts define required fields and types. Stable service IDs join the datasets; unique log, metric, deployment, incident, and runbook IDs support evidence citations. Use globally unique record IDs when adding scenarios. Additional scenarios can use sibling directories under `scenarios/` and matching evaluation files without reorganizing existing data. New evidence types or contract changes require explicit schema/validator updates.

## Service topology

```mermaid
graph TD
    checkout[checkout-service] -->|HTTP| payment[payment-service]
    checkout -->|HTTP| inventory[inventory-service]
    checkout -->|SQL| database[(database)]
    payment -->|SQL| database
    inventory -->|SQL| database
```

Edges point from caller to dependency. All four services are synthetic data definitions. The database is represented by topology and health logs rather than an HTTP metric series.

## Use Case 1

The observation period is January 15, 2026, 10:00–10:15 UTC (end exclusive).

| Time (UTC) | Event |
| --- | --- |
| 10:00–10:05 | Checkout v2.3.0 baseline: 0.1% 5xx |
| 10:05 | Successful checkout-service deployment to v2.3.1 |
| 10:06:15 | First sampled `java.lang.NullPointerException` in checkout discount handling |
| 10:06–10:15 | Checkout metric windows show 18% 5xx; downstream services stay healthy |
| 10:09 | Alert fires after three completed one-minute windows above 5% |

Evaluation ground truth: **checkout-service deployment regression**, affected service **checkout-service**, severity **SEV-2**. The faulty null handling is an injected scenario assumption supported by the deployment, exception, and metric timeline. SEV-2 is a supplied evaluation label, not a computed organization-wide severity policy.

## Evidence semantics and tools

| Tool | Data and useful filters |
| --- | --- |
| `search_logs()` | Scenario logs: service, time range, level, message keyword; trace_id correlates sampled observations |
| `get_metrics()` | Scenario metrics: service, metric name, time range; request/error counts, error rate, and p95 latency |
| `get_recent_deployments()` | Scenario deployment history: timestamp, service_id, versions, status |
| `get_dependencies()` | Shared directed topology edges and service definitions |
| `search_runbooks()` | Runbook title, tags, service_ids, symptoms, and investigation steps |

The Part 3 tools below query only Use Case 1. Time ranges are start-inclusive and end-exclusive.

Metric timestamps mark the start of fixed, non-overlapping 60-second windows. Rates are fractions (0.18 means 18%) and equal error counts divided by request counts; a zero-request window has rate zero. Latency is milliseconds. Logs are sampled observations, not an exhaustive request ledger, so log counts do not equal metric counts. A shared trace ID groups the minute's synthetic workflow observations; records are not spans or an exact causal call sequence. Database HTTP status is null. All timestamps use UTC; historic deployments can precede the observation interval.

Assumptions: rollout is instantaneous and successful; the regression first manifests a minute later; each application has 1,000 requests per minute; baseline errors are background noise; payment, inventory, and database remain healthy. No recovery or rollback is simulated. Fixtures are authored and committed directly, so no random seed, generator, credentials, or external infrastructure is needed.

## Run the backend

From the repository root, create and activate a virtual environment, then install and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[test]"
python3 -m uvicorn signal_trace.api.app:app --reload
```

The service runs at `http://127.0.0.1:8000`; interactive API documentation is at `/docs`.
`GET /health` returns HTTP 200 with `{"status":"ok"}`. Submit the existing incident fixture:

```bash
curl -X POST http://127.0.0.1:8000/incidents \
  -H 'Content-Type: application/json' \
  --data-binary @scenarios/uc1_deployment_5xx/incident.json
```

`POST /incidents` returns HTTP 200 with `incident_id`, `status: "accepted"`, and
`message: "Incident accepted. Triage is not implemented."` Invalid requests return
HTTP 422. Acceptance is stateless: nothing is persisted, queued, or investigated.

`api/app.py` assembles the application through `create_app()`, `api/` contains routes,
and `models/` contains strict Pydantic contracts. The request mirrors the Part 1
incident schema, reuses its UTC timestamp validator, and checks the observation/alert
interval. Evidence consistency, known service IDs, and alert metric windows remain
in the existing offline validator; the API does not read evidence or evaluation files.
`models/responses.py` defines a future `TriageResponse` with summary, optional cause,
affected service and severity, and evidence IDs. It is a model only, available as
JSON Schema through `TriageResponse.model_json_schema()`.

The original `signal_trace.main:app` entry point remains available for compatibility.

`config.py` loads service settings from environment variables; set
`SIGNAL_TRACE_APP_NAME` to override the API title (default: `Signal Trace`).
The original acceptance endpoint remains compatible. Part 5 adds `POST /incidents/triage` for synchronous investigation; provider settings and the CLI are described below.

## Tool Layer

Import the five functions from `signal_trace.tools`, or instantiate `ToolLayer(source=...)`
with an `OperationalSource` adapter. `tools/models.py` defines strict Pydantic input/output
contracts, `service.py` handles deterministic queries, and `source.py` hides fixture paths
and normalizes data. The default `SyntheticSource` reads only the existing shared environment,
runbooks, and Use Case 1 operational evidence; it uses the existing schemas and UTC validator
without loading evaluation answers or repeating offline cross-record validation.

| Tool | Contract |
| --- | --- |
| `search_logs(service, start_time, end_time, level=None, keyword=None)` | Returns `list[LogRecord]`: timestamp, service, level, message, optional trace ID, and log ID. Level is an exact uppercase filter; keyword is a case-insensitive message substring. |
| `get_metrics(service, start_time, end_time, metric_name=None)` | Returns `list[MetricRecord]`: timestamp, service, metric name, numeric value, unit, window length, and evidence ID. |
| `get_recent_deployments(service, since)` | Returns `list[DeploymentRecord]` since the inclusive timestamp, newest first: service, deployed version, timestamp, deployment ID, and metadata (previous version, status, environment). |
| `get_dependencies(service)` | Returns `Dependencies` with sorted direct downstream/upstream service names and `service_known`. No transitive traversal. |
| `search_runbooks(query, service=None, top_k=5)` | Preserves `list[RunbookRecord]` and its title, services, tags, symptoms, steps, ID, and source. With semantic retrieval configured, returns ranked chunks with additional `chunk_id`, `text`, `score`, and `metadata`. Without a database URL, retains Part 3 keyword retrieval. |

Metrics expose `request_count` (requests), `error_5xx_count` (requests), `error_5xx_rate`
(fraction), and `latency_p95_ms` (ms). Omit `metric_name` or pass `None` to retrieve
all four. A supplied name matches only that specific metric; unmatched names return an empty list. Each measurement retains its original metric evidence
ID and 60-second window. Filtering uses the window's start timestamp. Logs and metrics are
sorted chronologically with stable tie-breakers.

Pass timezone-aware Python `datetime` values; offsets normalize to UTC. Reversed ranges,
naive datetimes, blank strings, incorrect types, and invalid log levels raise Pydantic
`ValidationError`. Equal range endpoints return no records. Supported levels are `DEBUG`,
`INFO`, `WARNING`, `ERROR`, and `CRITICAL`; current fixtures contain only `INFO` and `ERROR`.
Service IDs match exactly. Unknown services return empty lists, or empty dependencies with
`service_known=False`; unmatched metrics and queries also return empty lists. Missing or
invalid source data raises `ToolDataError`, rather than appearing as a successful empty query.

```python
from datetime import datetime, timezone
from signal_trace.tools import search_logs

errors = search_logs(
    service="checkout-service",
    start_time=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
    end_time=datetime(2026, 1, 15, 10, 15, tzinfo=timezone.utc),
    level="ERROR",
    keyword="discount",
)
# Serialize any returned model with .model_dump(mode="json").
```

These tools do not write operational data, execute runbook steps, or invoke the agent.
Semantic search is read-only; corpus ingestion is a separate explicit command.

## Semantic retrieval (Part 4)

```text
Existing runbook JSON → validated Document → bounded chunks → local embeddings
                                                           ↓
                                              PostgreSQL + pgvector
                                                           ↓
query → query embedding → cosine search + service filter + threshold + top_k
                                                           ↓
                                         normalized chunks → search_runbooks()
```

`retrieval/ingestion.py` reuses the existing runbook validator, and indexes **only**
`runbooks/*.json`. It does not read scenario evidence or evaluation ground truth.
The existing runbook becomes six chunks: one combined symptom overview and five
complete troubleshooting steps. Each step retains the symptom context and a visible
heading. Long sections split at sentence boundaries where possible; a sentence that
exceeds the 100-word budget is explicitly marked as a hard split. Headings repeat in
every fragment, and stable section IDs, source, service lists, and fragment metadata
preserve provenance. Content-based IDs make ingestion repeatable. No new incident scenarios or runbooks were added.
The normalized `Document` contract allows another operational-document parser later.

`embeddings.py` defines a replaceable `EmbeddingProvider`. The supplied FastEmbed
adapter runs `BAAI/bge-small-en-v1.5` locally on CPU (384 dimensions). Its first use
downloads model artifacts; subsequent runs use the ignored `.cache/embeddings` directory.
No external inference API, API key, or generative model is used. Install the optional
`retrieval` extra before enabling semantic search.

`storage.py` implements `VectorReader`/`VectorWriter` using PostgreSQL and pgvector.
It stores chunk/document IDs, source, applicable services, content, vector, and metadata.
Explicit ingestion initializes the schema and atomically replaces this dedicated knowledge
corpus, removing stale chunks. A failed replacement preserves the previous snapshot.
Stored embedding identity/dimensions must match the query provider; changing models requires
re-ingestion. The SQL schema is packaged with the module.

`service.py` validates queries and retrieves exact cosine nearest neighbors. Search uses
read-only, repeatable-read database transactions and never initializes tables or ingests.
A SELECT-only role can query the two knowledge tables; ingestion requires a separate role
with schema/extension and write privileges. The Compose account is for local development.
Exact scanning is appropriate for this six-chunk corpus; no approximate index is needed yet.

The public retrieval contract is `search(query, service=None, top_k=5)`. `top_k` must be
an integer from 1 to 100. Service filtering happens before limiting; unknown services and
no matches return `[]`. Scores are cosine similarities in [-1, 1], not probabilities.
The configurable minimum score defaults to 0.55. Ties use chunk ID for deterministic ordering.
`search_runbooks()` returns up to `top_k` **chunks**, so multiple results can cite the same
runbook. Each preserves all Part 3 fields and adds relevant text, chunk ID, score, and metadata.
No SQL, paths, or database credentials are exposed in the tool contract.

Without `SIGNAL_TRACE_RETRIEVAL_DATABASE_URL`, the tool retains the Part 3 deterministic
keyword mode (all query tokens must match; sorted by runbook ID). This preserves offline
usage and tests. Setting the URL enables semantic retrieval; database/model/setup errors
raise `RetrievalError` and never silently fall back to keywords. Other tools are unchanged.

### Local setup

With Docker Compose installed:

```bash
source .venv/bin/activate
python -m pip install -e ".[test,retrieval]"
cp .env.example .env
# Change both matching local password values in .env if desired.
set -a
source .env
set +a
docker compose up -d --wait knowledge-db
python -m signal_trace.retrieval ingest
python -m signal_trace.retrieval search "rollout broke purchasing" --service checkout-service --top-k 2
```

An existing PostgreSQL installation with pgvector also works: set
`SIGNAL_TRACE_RETRIEVAL_DATABASE_URL` to its connection string and run ingestion.
The configured database should be dedicated to Signal Trace's local knowledge corpus.
Use `SIGNAL_TRACE_EMBEDDING_MODEL`, `SIGNAL_TRACE_EMBEDDING_CACHE_DIR`, and
`SIGNAL_TRACE_RETRIEVAL_MIN_SCORE` to override defaults. The application reads environment
variables; it does not automatically load `.env`. Keep `.env` out of Git.

```python
from signal_trace.tools import search_runbooks

# With the configured database URL and an ingested corpus:
chunks = search_runbooks("rollout broke purchasing", service="checkout-service", top_k=2)
for chunk in chunks:
    print(chunk.source, chunk.score, chunk.text)
```

Stop the local database with `docker compose stop knowledge-db`; the named volume retains
the corpus. Run ingestion again after changing runbooks or embedding models.

### Retrieval tests and limits

The normal test command below runs Parts 1–3 plus deterministic retrieval tests, and
explicitly skips database/model integration tests unless enabled. To run **all** tests,
use a separate disposable PostgreSQL/pgvector database (integration tests replace its
knowledge corpus and create a SELECT-only test role):

```bash
export SIGNAL_TRACE_TEST_DATABASE_URL='postgresql://USER:PASSWORD@localhost:5432/signal_trace_test'
export SIGNAL_TRACE_TEST_REAL_EMBEDDINGS=1
unset SIGNAL_TRACE_RETRIEVAL_DATABASE_URL
python -m unittest discover -s tests -v
```

Keep the normal retrieval URL unset during the suite because the unchanged Part 3 tests
exercise compatibility mode. New integration tests enable and exercise semantic retrieval
explicitly, including its tool wiring, no-keyword-overlap match, service filter, top-k,
no-match threshold, read-only database permissions, and atomic rollback.

The current corpus is deliberately small. Relevance scores and ordering are model-dependent;
a broad question can rank several useful steps similarly. The 0.55 threshold is a starting
point, not a general relevance guarantee. Model artifacts must be available locally after
the initial download; model changes require re-ingestion. Part 5 uses this retrieval through `search_runbooks`; retrieval scores are never used as hypothesis confidence.

### Chunking and relevance evaluation

The fixed evaluation set is `evaluation/retrieval/runbook_queries.json`: ten positive
chunk-level questions and five negative/unsupported questions. It is separate from
operational knowledge and is never ingested. The reproducible report includes all
queries, expected sections, top-three scores, actual returned results, and before/after
metrics: [retrieval quality report](reports/retrieval_quality.md).

After ingestion into a disposable database, reproduce the measurements with:

```bash
SIGNAL_TRACE_TEST_DATABASE_URL="$SIGNAL_TRACE_RETRIEVAL_DATABASE_URL" \
  python scripts/evaluate_retrieval.py --output reports/retrieval_final.json
```

This is a small development evaluation, not an independent held-out benchmark. The
chunk revision improves Recall@3 but reduces Recall@1; top-one selection is unreliable.
An unsupported database-exhaustion query without a service filter still passes the
0.55 threshold. Cosine scores must not be treated as confidence or proof of relevance.

## Validate and test

From the repository root with Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[test]"
python3 -m signal_trace.validation
python3 -m unittest discover -s tests -v
```

Validation checks schemas, timestamp formats, IDs, service/evidence references, metric arithmetic, observation boundaries, and completed alert windows. Tests verify the deployment-regression timeline and deliberately corrupt fixtures to exercise validation failures. The CLI also accepts `--root /path/to/repository`; fixtures stay in the repository rather than being packaged as Python resources.

## AI-assisted development specifications

`prompts/` stores concise, reusable implementation specifications. `build_synthetic_environment.md` records the Part 1 request and its scope constraints. `build_backend_skeleton.md` records the Part 2 backend scope. `build_tool_layer.md` records the Part 3 specification. `build_rag_retrieval.md` records the Part 4 specification. `build_agent_workflow.md` records Part 5 and the offline-first validation instruction. Routine debugging conversations are not stored here.


## Agent workflow (Part 5)

```text
Incident → assess state → select tool → call Tool Layer → accumulate evidence
                  ↑                                           ↓
                  └──────── reassess hypotheses ←──────────────┘
                              ↓ sufficient evidence or iteration limit
                      validated structured triage
```

`Investigator` owns typed `AgentState`: incident, evidence, full tool results,
hypotheses and missing information in `assessment`, iteration count, confidence,
and an assessment snapshot after each call. `InvestigationModel` supplies `assess`,
`select_tool`, and `finalize`. The loop has no fixed sequence and allows repeated
queries. Only the five existing tools are callable; their existing input contracts
validate arguments. Providers receive copies of state, never source adapters or paths.

Evidence retains exact normalized records, source IDs, tool names, and originating
call IDs. Content-derived evidence IDs distinguish measurements sharing one metric
record ID and preserve separate retrieved runbook chunks. Repeat results deduplicate
without losing call provenance. Empty responses are retained as tool results, not
invented observations. Hypotheses preserve supporting and contradicting references;
finalization validates references and preserves the last hypotheses and evidence.
`InvestigationResult` extends the existing `TriageResponse` contract.

The default **offline reference model is deterministic, not an LLM**. Its narrow
5xx policy checks measured impact, application errors, recent changes, and sampled
dependency health. Missing HTTP metrics lead to a dependency-log query; contradictory
dependency observations reduce deployment confidence. Timing alone cannot establish
regression. The policy contains no fixture service IDs, versions, known exception
signatures, or incident timestamps. It is only validated on UC1 and in-memory
counterfactuals of that evidence, not additional use cases. Confidence values are
heuristic, not calibrated probabilities. SEV-2 is a provisional local rule for
sustained threshold violations, not an organization-wide severity policy.

Run the complete offline trace (the shell supplies the incident; the Agent does not
open scenario files):

```bash
python -m signal_trace.agent --provider offline \
  < scenarios/uc1_deployment_5xx/incident.json > reports/uc1_agent_run.json
```

Or submit the same incident to `POST /incidents/triage`. This synchronous endpoint
returns the validated final result; the CLI and Python API also return full state.
`POST /incidents` retains the Part 2 acknowledgement behavior and legacy message.
No investigation state is persisted by the backend.

```python
from signal_trace.agent import Investigator, OfflineReferenceModel

run = Investigator(OfflineReferenceModel(), max_iterations=12).run(incident)
result = run.result
```

`SIGNAL_TRACE_AGENT_PROVIDER` defaults to `offline` and
`SIGNAL_TRACE_AGENT_MAX_ITERATIONS` defaults to 12. To use a separately provisioned
Ollama server, select `ollama`, set `SIGNAL_TRACE_AGENT_MODEL` to an installed model
name and optionally set `SIGNAL_TRACE_AGENT_BASE_URL` (default
`http://localhost:11434`). No generative baseline was previously selected, so no live
model is silently chosen or downloaded. CLI flags `--provider`, `--model`, and
`--max-iterations` override their respective settings. The adapter uses Ollama's
[structured chat API](https://docs.ollama.com/api/chat), isolated in `agent/provider.py`.
Provider errors propagate; there are no retries or silent fallbacks. Replacing it
requires implementing the three model-interface methods.

Runbook retrieval preserves Part 4 behavior: without a retrieval database URL it
uses keyword mode; with a configured database it uses semantic retrieval. The saved
representative run uses keyword mode. Retrieval is accessed only through the Tool
Layer. The Agent never loads evaluation ground truth, README, or prompt specifications.

See [the representative investigation](reports/uc1_agent_run.md),
[full state and trace](reports/uc1_agent_run.json), and
[complete validated triage JSON](reports/uc1_agent_triage.json).

**Validation boundary:** offline tests establish state transitions, branching,
repeated calls, evidence accumulation/provenance, hypothesis revision, stopping,
structured output, API wiring, and file-access isolation. Mocked provider tests
check request/response contracts only. Live LLM reasoning, tool-selection quality,
semantic interpretation of citations, and live end-to-end reliability remain
unverified. Citation existence does not prove that a live model's prose follows
from the cited evidence. The reference policy cannot prove code-level causation;
source diff/reproduction and recovery evidence remain missing.

No Parts 6–9 were added: no advanced guardrails, retries, evaluation harness,
observability infrastructure, persistence, or operational write/remediation actions.

The [Part 5 semantic RAG smoke test](reports/uc1_agent_semantic_smoke.md) verifies a complete UC1 investigation with real embeddings and PostgreSQL/pgvector, fallback blocked, and chunk provenance checked. The focused `tests/test_agent_semantic.py` integration test uses the same disposable-database and real-embedding flags as the full suite above.
