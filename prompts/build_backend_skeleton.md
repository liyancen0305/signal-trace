# Part 2: Backend / Project Skeleton

Add a Python backend using FastAPI and Pydantic under the existing `signal_trace`
package. Preserve all Part 1 fixtures, schemas, validation behavior, and tests.

- Use `signal_trace/api/app.py` as the FastAPI application entry point.
  Keep environment configuration, API routes, and models separate.
- Define an incoming incident model consistent with existing project concepts and
  a future structured triage response model with evidence references.
- Implement `GET /health` and `POST /incidents`. The latter only validates and
  acknowledges a request; no persistence or processing is required.
- Reuse existing validation where appropriate without repeating offline evidence
  checks. Keep the structure extensible for the future Part 3 tool layer.
- Update dependencies in `pyproject.toml`, add health and incident validation tests,
  and briefly document service startup and backend structure in README.
- Do not implement LLM calls, orchestration, tools, RAG, hypothesis generation,
  new evaluation logic, or additional incident use cases.

Report created/changed files and the structure, run all tests, and confirm Part 1
still passes. Do not implement Part 3 or push to GitHub without approval.
