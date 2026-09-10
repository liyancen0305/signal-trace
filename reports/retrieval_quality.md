# Signal Trace Part 4: chunking and retrieval quality

## Outcome

The revised chunks preserve complete troubleshooting steps with better standalone context.
All ten predefined target ideas appear within the returned top three, but only two rank first.
This is useful candidate retrieval, not reliable top-one selection or a calibrated relevance gate.
One of five unsupported queries still returns unrelated content above the 0.55 cutoff.

## Method

- Corpus: only `runbooks/deployment_5xx.json`; no new runbooks, scenarios, or evaluation answers were ingested.
- Real FastEmbed `BAAI/bge-small-en-v1.5` CPU embeddings (384 dimensions), FastEmbed 0.8.0, and PostgreSQL/pgvector exact cosine search.
- The 15 query judgments were authored before baseline retrieval and stayed unchanged. Ten positives have one expected troubleshooting idea; five negatives have none.
- Labels are in `evaluation/retrieval/runbook_queries.json`, outside the ingestion allowlist. Document-level recall would be trivial with one runbook, so metrics measure predefined chunk/section targets.
- Recall@k is the mean fraction of the single expected idea found within k returned results. MRR@3 averages reciprocal target rank; missing or below-threshold targets contribute zero. Negatives are excluded from recall/MRR and assessed separately.
- The original two symptom chunks both represent the symptom idea. New stable section IDs allow comparison despite changed chunk hashes.
- This is a small development evaluation, not a held-out benchmark: the set informed chunk revisions. Scores are cosine similarities, not confidence probabilities.
- The threshold remained 0.55 throughout. No labels or thresholds were changed to improve the result.

## Chunking assessment

The original seven chunks were 9–22 words including the repeated title. Two were isolated
symptom fragments. The fallback splitter could cut a sentence and omitted the heading on
continuation fragments.

The final six chunks are 15–34 words: one coherent symptom overview and five independent
actions. Every action includes symptom context; none mixes unrelated actions. All five
original steps remain intact, including the rollback evidence requirement and approval
condition. The source is intentionally short; padding it with invented instructions would
reduce grounding.

Each chunk preserves visible headings, document ID `rb-deployment-5xx`, source
`runbook:rb-deployment-5xx`, and the service list: `checkout-service`, `payment-service`,
`inventory-service`. Metadata includes the original runbook, heading, stable section ID,
section index, fragment index/count, word offset, and a hard-split indicator. Service names
and source remain structured metadata rather than repeated embedding text.

Long sections now prefer sentence boundaries and repeat headings in every fragment. An
individual sentence exceeding the word budget still requires a flagged hard split. None
of the six real chunks uses that fallback. The sentence detector is deliberately simple,
not a full linguistic parser.

### Representative final chunks

**SYM — 15 words**

```text
Investigate elevated 5xx after deployment

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

Section `symptoms`; source `runbook:rb-deployment-5xx`; complete fragment 1/1; no hard split.

**LOG — 27 words**

```text
Investigate elevated 5xx after deployment

Step 2: Search application logs for new exception signatures and affected versions.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

Section `step:2`; source `runbook:rb-deployment-5xx`; complete fragment 1/1; no hard split.

**DEP — 28 words**

```text
Investigate elevated 5xx after deployment

Step 3: Check dependency metrics and logs to distinguish local and downstream failures.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

Section `step:3`; source `runbook:rb-deployment-5xx`; complete fragment 1/1; no hard split.

**ROLL — 34 words**

```text
Investigate elevated 5xx after deployment

Step 4: If evidence supports a regression, propose rollback to the last healthy version through the normal approval process.

Symptoms: Elevated HTTP 5xx rate; New exceptions near a release.
```

Section `step:4`; source `runbook:rb-deployment-5xx`; complete fragment 1/1; no hard split.

## Retrieval metrics

| Variant | Recall@1 | Recall@3 | MRR@3 | Negatives returning results |
| --- | ---: | ---: | ---: | ---: |
| Original: seven fragments | 40% | 80% | 0.550 | 1/5 |
| Intermediate: context first | 10% | 100% | 0.517 | 1/5 |
| Final: action first | 20% | 100% | 0.567 | 1/5 |

The context-first trial improved top-three coverage but blurred action distinctions.
Putting the action first recovered some top-one quality. Final Recall@1 still regresses
relative to baseline; the tradeoff is retained explicitly, not hidden by relabeling queries.

## Every query: expectations and top-three scores

**Legend:** SYM = symptom overview; RATE = compare pre/post error rates; LOG = new
exception/version search; DEP = dependency checks; ROLL = evidence-supported rollback
through approval; VERIFY = sustained recovery. All candidates cite `rb-deployment-5xx`.

**†** marks a diagnostic candidate below 0.55, which the normal tool does **not** return.
A service-filtered empty candidate set is shown as “none.” Full text, chunk IDs, metadata,
source, services, and unrounded scores are in the accompanying JSON reports.

| ID | Query | Service filter | Expected | Raw top 3: section (score) | Expected in returned top 3? |
| --- | --- | --- | --- | --- | --- |
| q01 | checkout errors started after a new release | checkout-service | RATE | SYM (0.6367); RATE (0.6099); LOG (0.5995) | Yes, rank 2 |
| q02 | requests began failing shortly after shipping a new version | checkout-service | RATE | SYM (0.6934); RATE (0.6640); LOG (0.6490) | Yes, rank 2 |
| q03 | rollout broke purchasing | checkout-service | SYM | ROLL (0.5959); SYM (0.5580); VERIFY (0.5354)† | Yes, rank 2 |
| q04 | How do I locate the stack traces that first appeared with the updated build? | checkout-service | LOG | DEP (0.6935); SYM (0.6847); LOG (0.6762) | Yes, rank 3 |
| q05 | Which fault messages are new and which software revisions emit them? | checkout-service | LOG | SYM (0.7104); DEP (0.7022); LOG (0.7011) | Yes, rank 3 |
| q06 | Check whether dependent services are healthy before blaming the application. | payment-service | DEP | ROLL (0.6264); DEP (0.6184); SYM (0.6173) | Yes, rank 2 |
| q07 | How can I tell whether a problem comes from another service? | checkout-service | DEP | SYM (0.6940); DEP (0.6935); RATE (0.6781) | Yes, rank 2 |
| q08 | Can we revert to a working build once the change is confirmed as the cause? | checkout-service | ROLL | ROLL (0.6741); VERIFY (0.6656); SYM (0.6431) | Yes, rank 1 |
| q09 | What authorization is needed before undoing a faulty release? | inventory-service | ROLL | VERIFY (0.6481); ROLL (0.6403); SYM (0.6308) | Yes, rank 2 |
| q10 | How do I confirm the fix keeps working over successive measurements? | checkout-service | VERIFY | VERIFY (0.6629); DEP (0.6498); SYM (0.6484) | Yes, rank 1 |
| n01 | payment calls are timing out | payment-service | none | SYM (0.5461)†; RATE (0.5265)†; ROLL (0.5222)† | PASS: no result |
| n02 | database connections appear exhausted | database | none | none | PASS: no result |
| n03 | database connections appear exhausted | none | none | SYM (0.5811); DEP (0.5567); VERIFY (0.5318)† | FAIL: unsupported results |
| n04 | How do I reset a forgotten account password? | none | none | VERIFY (0.5002)†; SYM (0.4701)†; RATE (0.4467)† | PASS: no result |
| n05 | chocolate cake recipe and baking temperatures | none | none | RATE (0.4629)†; VERIFY (0.4603)†; ROLL (0.4530)† | PASS: no result |

## Failures and concerns

- **Top-one target misses:** q01, q02, q03, q04, q05, q06, q07, and q09. Content may be related, but it is not the predeclared target action. No positive target is missing from the final top three.
- **Unsupported operational question:** database connection exhaustion without a filter returns SYM at 0.5811 and DEP at 0.5567, despite no connection-pool runbook. The database filter correctly rejects it, but unfiltered relevance remains a failure.
- **No clean score separation:** the valid no-keyword-overlap query retrieves its target at 0.5580, below the unsupported database query’s top score. Raising the threshold enough to reject the latter would lose the former. The threshold was left unchanged.
- **Near-boundary negative:** payment timeouts score 0.5461, only just below the cutoff. Rejection in this run is not strong evidence of reliable abstention.
- **Semantic evidence:** `rollout broke purchasing` has no words in common with its returned chunks. This demonstrates semantic matching, not generalization across a large corpus.
- **Limited scope:** one runbook cannot establish cross-document discrimination. Broader human judgments and a held-out set are needed before trusting rankings or automated decisions.

## Changes and verification

- Improved chunk context, headings, stable section IDs, and sentence-aware splitting; re-ingested six final chunks.
- Added fixed judgments, a reproducible evaluator, baseline/intermediate/final JSON measurements, and a complete final chunk export.
- Added regression tests for intact steps, metadata, sentence boundaries, oversized sentences, section IDs, and metric arithmetic; updated chunk-count expectations and README.
- **Full suite: 77 passed, 0 failed, 0 skipped**, including all Parts 1–3 and real embedding/PostgreSQL/pgvector integration tests. Existing fixture validation also passed.
- The existing non-failing Starlette/httpx deprecation warning remains.
- No orchestration, Part 5, production write action, commit, or push.

## Reproduce

Use a disposable database with pgvector and the installed retrieval extra. Ingest first:

```bash
python -m signal_trace.retrieval ingest
SIGNAL_TRACE_TEST_DATABASE_URL="$SIGNAL_TRACE_RETRIEVAL_DATABASE_URL" \
  python scripts/evaluate_retrieval.py --output reports/retrieval_final.json
```

Final JSON is reproducible with the revised chunker. Baseline and intermediate JSON are
retained experiment artifacts; reproducing those requires their earlier chunking logic.
Model/version changes may alter scores.
