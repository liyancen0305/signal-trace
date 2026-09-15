# Part 8 validation

Part 8 adds passive structured tracing without changing Agent decisions. `InvestigationRun.trace` is JSON-compatible and includes timestamps, iteration records, LLM/tool calls, inputs/outputs, evidence deltas, hypothesis transitions, failures, retries, stopping reason, final result, and metrics. Sensitive key names are redacted.

## Representative normal trace

The normal UC1 run completed with `sufficient_evidence` after 9 iterations. It recorded 9 tool calls, 20 LLM calls (assessment, selection, and finalization), 78 evidence additions, 0 retries, and 0 failures. Total latency was approximately 161 ms; per-tool latency is recorded in [normal trace](part8_trace_example.json).

Hypothesis and confidence transitions are recorded in `hypothesis_changes`; each tool event records its ordered call ID, validated inputs, outputs, evidence IDs, and evidence added. The final structured result is embedded verbatim in `trace.final_result`.

## Failure trace

With `get_recent_deployments` forced to raise `TimeoutError`, the trace records the failed tool, retry attempt 2, missing-evidence-driven incomplete result, and `no_progress` stopping. The run remained traceable with 11 tool calls, 23 LLM calls, 1 retry, and 2 recorded failures (the injected timeout and the intentionally suppressed duplicate decision). See [failure trace](part8_failure_trace_example.json).

## Tests

The observability tests cover complete normal traces, ordered calls, evidence and hypothesis changes, failure/retry events, metrics, redaction, custom tracers, and result equivalence with tracing disabled. Full Parts 1–8 regression: **137 passed, 0 failed, 0 skipped** in 12.267 seconds. `git diff --check` passed.

## Limitations

Token usage and estimated model cost remain null for the offline provider and are populated only when a provider supplies usage metadata. Tool latency is measured around the synchronous caller and excludes work that continues after a deadline. Trace recording is best-effort and intentionally swallows recorder errors so observability cannot alter investigation behavior. In-memory traces are returned with the run; durable export, distributed trace correlation, sampling, and cross-process aggregation are not implemented.

No Part 9 work, commit, or push was performed.
