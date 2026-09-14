# Part 5 of Signal Trace: Agent Workflow / Orchestration

The repository already contains Parts 1–4: the synthetic production environment,
FastAPI backend, read-only Tool Layer, RAG / semantic retrieval, and Use Case 1
incident data with isolated evaluation ground truth.

## Goal

Build the first end-to-end investigation workflow for **Use Case 1 only**:

1. Receive an incident and inspect its current state.
2. Decide what information is needed next and call an existing tool.
3. Collect and preserve evidence.
4. Generate or update root-cause hypotheses.
5. Determine whether more evidence is needed; continue or stop.
6. Produce a structured triage result.

## Agent state

Maintain explicit, typed state containing at least the incident, collected evidence,
tool results, current hypotheses, missing information, iteration count, and current
confidence.

## Investigation workflow

Incident → understand incident → inspect evidence → decide next tool → call tool →
update evidence/state → generate/update hypotheses → determine whether more evidence
is needed → continue or stop → generate final structured triage result.

Support branching and repeated investigation steps. Do not hard-code a fixed tool
sequence specifically for Use Case 1.

## Tools and model integration

Use only the existing `search_logs`, `get_metrics`, `get_recent_deployments`,
`get_dependencies`, and `search_runbooks` tools. The Agent must not bypass the Tool
Layer or directly read synthetic environment files.

Provide a clean model abstraction for reasoning, tool selection, hypothesis
generation/update, and final triage generation. Isolate provider-specific logic so
the model can be replaced later. Use the project's selected baseline model/configuration
if available.

## Evidence grounding and hypotheses

Ground important conclusions in tool-returned evidence; preserve supporting evidence
references. Do not invent operational facts or conclude that deployment caused an
incident merely because timestamps are close. Consider other available evidence.

Support a primary hypothesis, alternatives, supporting and contradicting evidence,
confidence, and updates as evidence arrives. Revise earlier hypotheses when new
evidence contradicts them.

## Stopping and output

Stop when sufficient evidence exists for a reasonable triage result or the basic
iteration limit is reached. Keep stopping simple; advanced reliability belongs to Part 6.

Return a validated structured result containing at least severity, affected_services,
primary_hypothesis / likely_root_cause, confidence, supporting_evidence,
alternative_hypotheses, missing_information, and recommended_actions. Reuse existing
Pydantic models where appropriate.

## Scope exclusions

Use Case 1 only. Do not implement additional use cases, production remediation, write
operations, advanced retries/fallbacks, full guardrails, Part 6 reliability, Part 7
evaluation harness, or Part 8 observability. Do not expose evaluation ground truth
to the Agent. Do not implement Parts 6–9.

## Tests

Add coverage for successful end-to-end Use Case 1 investigation, Agent state updates,
expected tool invocation, evidence accumulation, hypothesis creation/update, structured
output validation, expected affected service and deployment-regression hypothesis,
evidence provenance, no direct access to evaluation ground truth, and no direct access
to raw operational files outside the Tool Layer. Preserve all Parts 1–4 tests.

## Representative run and documentation

Run Use Case 1 end-to-end and report the initial incident, tool-call sequence,
important evidence, hypothesis changes, stopping decision, and final structured
triage result. Update README with a concise workflow explanation. Save this specification
as `prompts/build_agent_workflow.md`.

Before completion verify Tool Layer-only access, no fixed known-answer workflow,
preserved provenance, evidence-based hypotheses, validated structured results,
evaluation isolation, existing regressions passing, expected general UC1 diagnosis,
and no Parts 6–9 work.

When finished, show all files created/changed; explain the architecture; show the UC1
trace and final triage; run the complete suite and report passed/failed tests; report
assumptions, weaknesses, and remaining limitations. Do not commit or push until approved.

## Follow-up instruction: offline validation first

Proceed with the offline reference model first. Validate the complete Part 5 workflow,
state transitions, tool invocation, evidence accumulation, hypothesis updates, stopping
logic, and structured output offline. Keep generative integration behind a clean
provider abstraction for a later live model. Clearly distinguish offline-validated
behaviors from behaviors requiring a live LLM run. Do not mark live LLM reasoning or
tool-selection quality as verified.
