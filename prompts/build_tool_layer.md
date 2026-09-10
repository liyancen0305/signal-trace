# Part 3: Tool Layer

Preserve Parts 1 and 2: synthetic environment, Use Case 1 evidence, schemas,
validation, FastAPI backend, and all existing tests. Add a clean, read-only Tool
Layer between the future AI Agent and operational data.

Implement typed inputs and normalized structured outputs for:

- `search_logs(service: str, start_time: datetime, end_time: datetime,
  level: Optional[str] = None, keyword: Optional[str] = None)`: timestamp,
  service, level, message, and trace ID when available.
- `get_metrics(service: str, start_time: datetime, end_time: datetime,
  metric_name: Optional[str] = None)`: timestamp, service, metric name,
  numeric value, and unit when available.
- `get_recent_deployments(service: str, since: datetime)`: service, version,
  deployment timestamp, and available deployment metadata.
- `get_dependencies(service: str)`: direct downstream dependencies and,
  where supported by existing topology, direct upstream dependencies.
- `search_runbooks(query: str, service: Optional[str] = None)`: deterministic
  text/keyword matching returning matching runbooks, relevant content,
  applicable services, and source/reference.

Validate parameters, including reversed time ranges. Handle unknown services and
empty results cleanly. Hide JSON/file layouts behind stable contracts so real
operational adapters can replace synthetic data later. Reuse existing schemas and
validation where appropriate. Query only the existing environment, runbooks, and
Use Case 1 operational evidence; do not expose evaluation answers.

Do not implement LLM calls, orchestration, hypothesis generation, embeddings,
vector search, RAG, additional incident use cases, or production writes.

Add tests for valid calls, service/time/level/keyword/metric filtering, empty
results, invalid inputs, unknown services, dependencies, and runbooks. Preserve
and run the full Parts 1 and 2 test suite. Briefly document contracts and structure
in README. Report created/changed files, explain each tool, and confirm all tests
pass. Do not implement Part 4 or push to GitHub without approval.
