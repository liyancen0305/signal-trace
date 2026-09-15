# Agent evaluation report

Deterministic scoring only; confidence is not a probability.

| Scenario | Root | Services | Severity | Grounding | Confidence | Overall |
| --- | --- | --- | --- | --- | --- | --- |
| Misleading correlation | False | True | True | True | 0.55 | False |
| Downstream dependency failure | False | False | True | True | 0.6 | False |
| Deployment regression | True | True | True | True | 0.88 | True |
| Missing or failed data source | True | True | True | True | 0.55 | False |
| Resource exhaustion | False | True | True | True | 0.55 | False |

## Aggregate

```json
{
  "cases": 5,
  "overall_pass_rate": 0.2,
  "root_cause_accuracy": 0.4,
  "affected_service_accuracy": 0.8,
  "severity_accuracy": 1.0,
  "grounding_pass_rate": 1.0,
  "unsupported_claim_rate": 0.0,
  "unsupported_claim_rate_denominator": "cases with at least one unsupported or unverified claim / all cases",
  "average_tool_calls": 10.6,
  "repeated_call_rate": 0.07547169811320754,
  "repeated_call_rate_denominator": "repeated normalized decisions / all tool decisions (includes suppressed calls; excludes retries)",
  "high_confidence_wrong_answer_count": 0,
  "high_confidence_missing_evidence_count": 0,
  "failure_handling_pass_rate": 0.0,
  "failure_handling_cases": 1
}
```

## Misleading correlation

[Per-case metrics](correlation.json) · [Full trace and independent tool ledger](correlation.trace.json)

**Expected:** checkout-service memory exhaustion

**Actual:** Local application failure in checkout-service; trigger not established.

**Tools:** get_metrics(checkout-service) → search_logs(checkout-service) → get_recent_deployments(checkout-service) → get_dependencies(checkout-service) → get_metrics(database) → search_logs(database) → get_metrics(inventory-service) → get_metrics(payment-service) → search_runbooks(checkout-service) → get_metrics(checkout-service) → get_metrics(checkout-service)

```json
{
  "scores": {
    "root_cause": false,
    "affected_services": true,
    "severity": true,
    "important_evidence": false,
    "outcome": false
  },
  "missing_important_evidence": [
    "eval-heap-pressure",
    "log-checkout-service-06"
  ],
  "unsupported_claims": [],
  "tool_usage": {
    "pass": false,
    "missing_sources": [],
    "repeated_calls": 1,
    "retry_attempts": 0,
    "unnecessary_calls_over_budget": 1,
    "no_new_evidence_calls": [],
    "decision_count": 11,
    "actual_tool_attempts": 10
  },
  "stopping": {
    "pass": false,
    "too_early": false,
    "unnecessary_continuation": true,
    "iteration_limit": false,
    "reason": "no_progress"
  },
  "confidence_quality": {
    "pass": true,
    "high_confidence_wrong": false,
    "high_confidence_missing": false,
    "overconfident_conflict": false,
    "value": 0.55,
    "probability_interpretation": false
  },
  "reliability": {
    "applicable": false,
    "pass": null,
    "missing_information_reported": true,
    "human_review_recommended": false
  }
}
```

## Downstream dependency failure

[Per-case metrics](dependency.json) · [Full trace and independent tool ledger](dependency.trace.json)

**Expected:** payment-service outage propagating to checkout-service

**Actual:** A downstream failure may be propagating to the alerted service.

**Tools:** get_metrics(checkout-service) → search_logs(checkout-service) → get_recent_deployments(checkout-service) → get_dependencies(checkout-service) → get_metrics(database) → search_logs(database) → get_metrics(inventory-service) → get_metrics(payment-service) → search_runbooks(checkout-service) → get_metrics(checkout-service) → get_metrics(checkout-service)

```json
{
  "scores": {
    "root_cause": false,
    "affected_services": false,
    "severity": true,
    "important_evidence": false,
    "outcome": false
  },
  "missing_important_evidence": [
    "log-checkout-service-06",
    "metric-payment-service-06"
  ],
  "unsupported_claims": [],
  "tool_usage": {
    "pass": false,
    "missing_sources": [],
    "repeated_calls": 1,
    "retry_attempts": 0,
    "unnecessary_calls_over_budget": 1,
    "no_new_evidence_calls": [],
    "decision_count": 11,
    "actual_tool_attempts": 10
  },
  "stopping": {
    "pass": false,
    "too_early": false,
    "unnecessary_continuation": true,
    "iteration_limit": false,
    "reason": "no_progress"
  },
  "confidence_quality": {
    "pass": true,
    "high_confidence_wrong": false,
    "high_confidence_missing": false,
    "overconfident_conflict": false,
    "value": 0.6,
    "probability_interpretation": false
  },
  "reliability": {
    "applicable": false,
    "pass": null,
    "missing_information_reported": true,
    "human_review_recommended": false
  }
}
```

## Deployment regression

[Per-case metrics](deployment.json) · [Full trace and independent tool ledger](deployment.trace.json)

**Expected:** checkout-service deployment regression

**Actual:** Likely deployment regression in checkout-service after version 2.3.1. Observed error: Checkout failed at CheckoutHandler.applyDiscount: discount.code is null

**Tools:** get_metrics(checkout-service) → search_logs(checkout-service) → get_recent_deployments(checkout-service) → get_dependencies(checkout-service) → get_metrics(database) → search_logs(database) → get_metrics(inventory-service) → get_metrics(payment-service) → search_runbooks(checkout-service)

```json
{
  "scores": {
    "root_cause": true,
    "affected_services": true,
    "severity": true,
    "important_evidence": true,
    "outcome": true
  },
  "missing_important_evidence": [],
  "unsupported_claims": [],
  "tool_usage": {
    "pass": true,
    "missing_sources": [],
    "repeated_calls": 0,
    "retry_attempts": 0,
    "unnecessary_calls_over_budget": 0,
    "no_new_evidence_calls": [],
    "decision_count": 9,
    "actual_tool_attempts": 9
  },
  "stopping": {
    "pass": true,
    "too_early": false,
    "unnecessary_continuation": false,
    "iteration_limit": false,
    "reason": "sufficient_evidence"
  },
  "confidence_quality": {
    "pass": true,
    "high_confidence_wrong": false,
    "high_confidence_missing": false,
    "overconfident_conflict": false,
    "value": 0.88,
    "probability_interpretation": false
  },
  "reliability": {
    "applicable": false,
    "pass": null,
    "missing_information_reported": true,
    "human_review_recommended": true
  }
}
```

## Missing or failed data source

[Per-case metrics](missing.json) · [Full trace and independent tool ledger](missing.trace.json)

**Expected:** unknown trigger; local application failure only

**Actual:** Local application failure in checkout-service; trigger not established.

**Tools:** get_metrics(checkout-service) → search_logs(checkout-service) → get_recent_deployments(checkout-service) → get_dependencies(checkout-service) → get_metrics(database) → search_logs(database) → get_metrics(inventory-service) → get_metrics(payment-service) → search_runbooks(checkout-service) → get_metrics(checkout-service) → get_metrics(checkout-service)

```json
{
  "scores": {
    "root_cause": true,
    "affected_services": true,
    "severity": true,
    "important_evidence": true,
    "outcome": true
  },
  "missing_important_evidence": [],
  "unsupported_claims": [],
  "tool_usage": {
    "pass": false,
    "missing_sources": [],
    "repeated_calls": 1,
    "retry_attempts": 1,
    "unnecessary_calls_over_budget": 1,
    "no_new_evidence_calls": [],
    "decision_count": 11,
    "actual_tool_attempts": 11
  },
  "stopping": {
    "pass": false,
    "too_early": false,
    "unnecessary_continuation": true,
    "iteration_limit": false,
    "reason": "no_progress"
  },
  "confidence_quality": {
    "pass": true,
    "high_confidence_wrong": false,
    "high_confidence_missing": false,
    "overconfident_conflict": false,
    "value": 0.55,
    "probability_interpretation": false
  },
  "reliability": {
    "applicable": true,
    "pass": false,
    "missing_information_reported": true,
    "human_review_recommended": false
  }
}
```

## Resource exhaustion

[Per-case metrics](resource.json) · [Full trace and independent tool ledger](resource.trace.json)

**Expected:** checkout-service memory exhaustion

**Actual:** Local application failure in checkout-service; trigger not established.

**Tools:** get_metrics(checkout-service) → search_logs(checkout-service) → get_recent_deployments(checkout-service) → get_dependencies(checkout-service) → get_metrics(database) → search_logs(database) → get_metrics(inventory-service) → get_metrics(payment-service) → search_runbooks(checkout-service) → get_metrics(checkout-service) → get_metrics(checkout-service)

```json
{
  "scores": {
    "root_cause": false,
    "affected_services": true,
    "severity": true,
    "important_evidence": false,
    "outcome": false
  },
  "missing_important_evidence": [
    "eval-heap-pressure"
  ],
  "unsupported_claims": [],
  "tool_usage": {
    "pass": false,
    "missing_sources": [],
    "repeated_calls": 1,
    "retry_attempts": 0,
    "unnecessary_calls_over_budget": 1,
    "no_new_evidence_calls": [],
    "decision_count": 11,
    "actual_tool_attempts": 10
  },
  "stopping": {
    "pass": false,
    "too_early": false,
    "unnecessary_continuation": true,
    "iteration_limit": false,
    "reason": "no_progress"
  },
  "confidence_quality": {
    "pass": true,
    "high_confidence_wrong": false,
    "high_confidence_missing": false,
    "overconfident_conflict": false,
    "value": 0.55,
    "probability_interpretation": false
  },
  "reliability": {
    "applicable": false,
    "pass": null,
    "missing_information_reported": true,
    "human_review_recommended": false
  }
}
```
