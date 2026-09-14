# Use Case 1: representative Part 5 investigation

Executed with **offline-reference-v1**, a deterministic policy, not an LLM. Runbook retrieval used Part 3 keyword mode. No evaluation data was supplied to the Agent.

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

## Actual tool-call sequence

| Step | Tool | Service | Returned records | Decision |
| --- | --- | --- | ---: | --- |
| call-1 | get_metrics | checkout-service | 15 | Measure impact and baseline |
| call-2 | search_logs | checkout-service | 15 | Inspect error signatures across baseline and incident |
| call-3 | get_recent_deployments | checkout-service | 1 | Check changes as a possible trigger, not proof of cause |
| call-4 | get_dependencies | checkout-service | 1 | Identify competing downstream explanations |
| call-5 | get_metrics | database | 0 | Compare downstream error rates |
| call-6 | search_logs | database | 15 | No HTTP metric series; inspect dependency health logs |
| call-7 | get_metrics | inventory-service | 15 | Compare downstream error rates |
| call-8 | get_metrics | payment-service | 15 | Compare downstream error rates |
| call-9 | search_runbooks | checkout-service | 1 | Retrieve investigation and mitigation guidance |

78 distinct evidence items retained. The empty database metrics response is preserved as a tool result and leads to a database-log query. Complete call arguments and exact returned records are in [the full trace](uc1_agent_run.json).

## Important evidence

| Source record | Tool | Observation |
| --- | --- | --- |
| dep-checkout-231 | get_recent_deployments | 2026-01-15T10:05:00Z: 2.3.0 → 2.3.1; succeeded |
| metric-checkout-service-00 | get_metrics | checkout-service error_5xx_rate=0.001 at 2026-01-15T10:00:00Z (60s window) |
| metric-checkout-service-06 | get_metrics | checkout-service error_5xx_rate=0.18 at 2026-01-15T10:06:00Z (60s window) |
| log-checkout-service-06 | search_logs | 2026-01-15T10:06:15Z: Checkout failed at CheckoutHandler.applyDiscount: discount.code is null |
| log-database-14 | search_logs | 2026-01-15T10:14:15Z: Connection pool healthy; query completed |
| metric-inventory-service-14 | get_metrics | inventory-service error_5xx_rate=0.001 at 2026-01-15T10:14:00Z (60s window) |
| metric-payment-service-14 | get_metrics | payment-service error_5xx_rate=0.001 at 2026-01-15T10:14:00Z (60s window) |
| topology:checkout-service | get_dependencies | Downstream: database, inventory-service, payment-service |
| rb-deployment-5xx | search_runbooks | Investigate elevated 5xx after deployment; guidance, not operational proof |

The complete service metrics show a baseline 0.001 (0.1%) 5xx rate and 0.18 (18%) from 10:06 onward. Repeated application errors support a local failure; downstream measurements and explicitly healthy database logs weaken the dependency explanation. The Tool Layer does not expose the raw exception-type or log version fields, so the Agent does not claim those facts.

## Hypothesis updates

| Iteration | Primary | Local failure | Dependency failure | Deployment regression |
| ---: | --- | ---: | ---: | ---: |
| 0 | None | — | — | — |
| 1 | dependency-failure | 0.3 | 0.35 | — |
| 2 | local-failure | 0.55 | 0.35 | — |
| 3 | local-failure | 0.55 | 0.35 | 0.55 |
| 4 | local-failure | 0.55 | 0.35 | 0.55 |
| 5 | local-failure | 0.55 | 0.35 | 0.55 |
| 6 | local-failure | 0.55 | 0.35 | 0.55 |
| 7 | local-failure | 0.55 | 0.35 | 0.55 |
| 8 | deployment-regression | 0.55 | 0.15 | 0.88 |
| 9 | deployment-regression | 0.55 | 0.15 | 0.88 |

Confidence scores are fixed reference-policy heuristics, not probabilities. The deployment candidate stays at 0.55 while dependency evidence is missing; it reaches 0.88 only after the independent dependency checks. A counterfactual test adds adverse payment metrics and verifies that deployment confidence falls to 0.20 and the dependency hypothesis becomes primary.

## Stopping decision

`sufficient_evidence` after 9 of 12 allowed calls. Independent observations support a reasonable triage hypothesis; code-level confirmation remains missing. Other tests exhaust the iteration limit and return incomplete structured results.

## Final triage

[Complete validated result with exact cited records](uc1_agent_triage.json). The following is a compact projection; source IDs are displayed for readability.

```json
{
  "incident_id": "uc1-deployment-5xx",
  "severity": "SEV-2",
  "severity_rationale": "Provisional local policy: sustained 5xx above the alert threshold is SEV-2; not an organization-wide severity classification.",
  "affected_services": [
    "checkout-service"
  ],
  "primary_hypothesis": "Likely deployment regression in checkout-service after version 2.3.1. Observed error: Checkout failed at CheckoutHandler.applyDiscount: discount.code is null",
  "confidence": 0.88,
  "supporting_evidence": [
    "dep-checkout-231",
    "metric-checkout-service-00",
    "metric-checkout-service-06",
    "log-checkout-service-06",
    "log-database-14",
    "metric-inventory-service-14",
    "metric-payment-service-14"
  ],
  "alternative_hypotheses": [
    {
      "description": "Local application failure in checkout-service; trigger not established.",
      "confidence": 0.55,
      "supporting_evidence": [
        "metric-checkout-service-06",
        "metric-checkout-service-14",
        "log-checkout-service-06"
      ],
      "contradicting_evidence": []
    },
    {
      "description": "A downstream failure may be propagating to the alerted service.",
      "confidence": 0.15,
      "supporting_evidence": [
        "metric-checkout-service-06",
        "metric-checkout-service-14"
      ],
      "contradicting_evidence": [
        "log-database-14",
        "metric-inventory-service-14",
        "metric-payment-service-14"
      ]
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

## Validation and limitations

The complete suite passed **98 tests, 0 failed, 0 skipped**, including 21 new Agent tests and all 77 existing tests. PostgreSQL/pgvector and the cached real embedding model were enabled for the existing integration tests. The initial run without a database passed 86 tests and skipped eight; the final configured run executes them all.

```bash
SIGNAL_TRACE_TEST_DATABASE_URL='postgresql:///signal_trace_part5_test?host=/tmp/signal-trace-pg-socket&port=55432' \
SIGNAL_TRACE_TEST_REAL_EMBEDDINGS=1 \
.venv/bin/python -m unittest discover -s tests -v
```

Offline validated: end-to-end UC1, state snapshots and confidence updates, all five tools, evidence-driven branching, repeated calls and deduplication, exact provenance, hypothesis creation/revision, both stopping reasons, structured validation, API integration, and no evaluation/raw-file access outside tools. Mocked Ollama tests check all three provider phases, schema requests, parsing, and error propagation. Retrieved chunk provenance is tested with a controlled retriever; the existing Part 4 suite tests actual semantic retrieval.

**Not verified:** live LLM reasoning, live tool-selection quality, live model citation interpretation, live structured-output compliance, and live end-to-end performance. No live generative request was made. These tests do not establish general diagnostic quality beyond UC1 and its in-memory counterfactuals.

The reference policy uses supplied observation windows and direct dependencies. Healthy samples are partial evidence, not proof of absence of downstream failures. SEV-2 is provisional. Confidence is uncalibrated; the code/configuration diff and reproduction are still needed. Unknown evidence can cause repeated checks until the iteration limit. Provider/tool errors propagate. The backend is synchronous and stateless. No Part 6–9 systems, evaluation harness, remediation, operational writes, retries, or commits were added.
