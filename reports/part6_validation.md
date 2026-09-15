# Part 6 validation

Validated 2026-09-15. No Agent implementation changes, Part 7 work, commits, or pushes.

## Verdict

All exercised Part 6 failure behaviors passed. No regression was observed in normal UC1. This validates deterministic reference-policy behavior and guardrail enforcement, not arbitrary live LLM reasoning.

## Regression and skipped-test review

The previously saved Part 5 log contained 99 passed, zero failed, zero skipped. No saved Part 6 log existed at the start of this validation.

- Fresh default run: **104 passed, 0 failed, 9 skipped**, 113 discovered, 6.715 seconds. See [initial log](part6_initial_regression.log).
- Complete configured Parts 1–6 run: **113 passed, 0 failed, 0 skipped**, 10.696 seconds. See [full log](part6_regression.log).
- All 14 tests in `ReliabilityTests` passed. The `skipped` tool-result status in the duplicate-call test is intentional runtime suppression, not a skipped unittest.

Every initial skip is listed below. All were expected environment gates; none was counted as passing. All nine subsequently executed and passed.

| Class / test name | Initial skip reason | Expected? | Behavior left unverified by the initial run |
| --- | --- | --- | --- |
| AgentSemanticIntegrationTests.test_configured_semantic_retrieval_enters_agent_state_without_fallback | Disposable database and real embeddings not enabled | Yes | Part 6 preservation of the real retrieval → AgentState path |
| PostgresRetrievalTests.test_cosine_filter_top_k_and_tie_order | SIGNAL_TRACE_TEST_DATABASE_URL unset | Yes | PostgreSQL ranking/filtering regression; no direct Part 6 fault behavior |
| PostgresRetrievalTests.test_embedding_model_and_dimension_mismatch | SIGNAL_TRACE_TEST_DATABASE_URL unset | Yes | Storage mismatch rejection; no direct Part 6 fault behavior |
| PostgresRetrievalTests.test_failed_transaction_retains_previous_corpus | SIGNAL_TRACE_TEST_DATABASE_URL unset | Yes | Transaction preservation; no direct Part 6 fault behavior |
| PostgresRetrievalTests.test_reingestion_replaces_stale_chunks_and_empty_corpus | SIGNAL_TRACE_TEST_DATABASE_URL unset | Yes | Corpus replacement; no direct Part 6 fault behavior |
| PostgresRetrievalTests.test_search_with_select_only_database_role | SIGNAL_TRACE_TEST_DATABASE_URL unset | Yes | Read-only database access; no direct Part 6 fault behavior |
| RealSemanticRetrievalTests.test_real_semantic_match_without_exact_words | Real embedding integration not enabled; database also unset | Yes | Real semantic matching regression; no direct Part 6 fault behavior |
| RealSemanticRetrievalTests.test_real_service_top_k_empty_and_tool_contract | Real embedding integration not enabled; database also unset | Yes | Real retrieval Tool Layer contract; no direct Part 6 fault behavior |
| RealSemanticRetrievalTests.test_second_query_returns_operational_guidance | Real embedding integration not enabled; database also unset | Yes | Real guidance retrieval regression; no direct Part 6 fault behavior |

Final run enabled `SIGNAL_TRACE_TEST_REAL_EMBEDDINGS=1` and `SIGNAL_TRACE_TEST_DATABASE_URL=postgresql:///signal_trace_part5_test?host=/tmp/signal-trace-pg-socket&port=55432`, using the existing disposable PostgreSQL/pgvector runtime and cached embeddings. No final skip remains to justify.

## Representative traces

Reproduce with `.venv/bin/python scripts/validate_part6.py` while the disposable database is running. [Complete state, records, transitions, and results](part6_scenarios.json) are retained for each case. The script asserts exact tool-record provenance, references available at each transition, valid structured output, and final preservation of collected evidence.

### A — Tool failure and recovery

Injected `TimeoutError` into `get_recent_deployments` for checkout-service:

1. Metrics and logs establish a provisional local failure at 0.55 confidence; AgentState contains 30 records.
2. Iteration 3: timeout → one retry → timeout again. Tool result is `failed`, attempts=2; both timeout issues are recorded.
3. Missing information explicitly records `get_recent_deployments ...: failed`. Evidence count remains 30: no deployment records enter AgentState.
4. Confidence stays 0.55 at the failure transition (already below the 0.6 cap), versus 0.88 for the successful control. It never acquires an unsupported deployment/version explanation.
5. Remaining tools collect usable observations; final state contains 122 records. Duplicate suppression stops at iteration 11 with `no_progress`.
6. Final outcome: `insufficient_evidence`, confidence 0.55, provisional cause “Local application failure in checkout-service; trigger not established.” The only recommendation is to collect missing information before choosing mitigation.

Separate recovery variant: first metrics call times out, retry succeeds, attempts=2. The transient issue remains recorded; no unresolved gap is needed for the recovered call. Normal 9-iteration result and 0.88 confidence return. This checks that a recovered outage does not unnecessarily lower confidence.

### B — Conflicting evidence

Changed payment-service error-rate records to 0.4 in memory, retaining ordinary checkout deployment, baseline, and error observations.

- Before contradiction (iterations 3–7): deployment-regression hypothesis has confidence 0.55, supported by deployment timing, baseline metrics, elevated checkout metrics, and application error logs.
- Iteration 8: payment-service metrics arrive. Counterevidence `ev-ba5b1f64d2de3ddd55ce` is attached to the local-failure and deployment-regression hypotheses.
- Deployment confidence drops **0.55 → 0.20**. The primary hypothesis changes to possible downstream propagation, confidence **0.60**, with healthy observations from other dependencies retained as counterevidence.
- Final outcome: `insufficient_evidence`; the result explicitly requests resolution of conflicting evidence. It stops at iteration 11 with `no_progress` and 123 preserved records. It does not confirm either deployment or downstream failure as the cause.

The separate reliability test also tries to discard a prior contradiction and claim 0.99 confidence; preservation and the 0.6 cap pass.

### C — Insufficient evidence

Returned an empty list for all checkout-service metrics requests while allowing other sources to work.

- First tool result: `empty`, zero records and zero evidence IDs. Missing information records the empty request and missing service error-rate measurements.
- Other sources populate AgentState with 63 records, but no hypothesis gains sufficient support; all assessment transitions have no hypotheses.
- Final confidence is **0.00**, compared with control **0.88**; root cause and primary hypothesis are **null**, severity `UNKNOWN`.
- Final outcome is `insufficient_evidence`, with a collect-missing-information recommendation. It stops at iteration 11 via duplicate suppression. No unsupported root cause or missing metrics are invented.

## Part 5 preservation

Normal UC1 completes in 9 iterations at 0.88 confidence, SEV-2, with the same likely checkout deployment regression and observed null-discount error.

- Tool calls and arguments exactly match the saved Part 5 run: checkout metrics → checkout logs → deployments → topology → database metrics → database logs → inventory metrics → payment metrics → runbook search.
- All 78 AgentState evidence records exactly match the saved baseline, including provenance.
- Primary and alternative hypotheses, root cause, confidence, severity, affected services, and recommendations exactly match the baseline.
- Hypotheses evolve from tentative impact/dependency explanations to the corroborated deployment hypothesis; stopping remains `sufficient_evidence` after 9 iterations.
- Structured result validation and the triage endpoint test pass.
- Real semantic UC1 also succeeds in 9 iterations at 0.88, preserving 82 records including retrieved chunks. Instrumentation confirms Tool Layer routing, real query embeddings and pgvector search, no keyword fallback, and no evaluation/raw-runbook reads during investigation.
- Ground-truth isolation tests pass: in-memory investigations perform no file reads; real-source reads occur through the Tool Layer and never access evaluation files. This is application-path isolation, not an OS security boundary.

Expected additive Part 6 differences: tool statuses/attempts, reliability issues, outcome field, explicit empty database-metrics gap, and final preservation of all collected records. The absent HTTP metrics for the database still lead to health-log inspection and do not prevent normal completion.

## Remaining limitations

- These scenarios use the deterministic reference model; live Ollama transport is mocked in the suite. Arbitrary live model interpretation, contradiction detection, and natural-language claims are not proven by these results.
- Confidence values and the 0.6 cap are heuristics, not calibrated probabilities. Citation validity proves provenance, not that every semantic claim follows from a cited record.
- Timeout workers cannot be forcibly cancelled and may finish in the background. Service-wide concurrency/resource exhaustion is not validated.
- Incomplete results can retain an explicitly provisional hypothesis in `root_cause`; consumers must honor `outcome` and confidence.
- Duplicate suppression is based on equivalent normalized requests. Broader queries can still run before a duplicate is detected, explaining the extra evidence in incomplete cases.
- A Starlette/httpx deprecation warning appeared during validation; it caused no failed or skipped test.

Only validation scripts and reports were added. Existing implementation edits were preserved. `git diff --check` passed.
