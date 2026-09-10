# Part 1: synthetic production environment

Build Signal Trace, an evidence-grounded AI incident investigation project, with only Use Case 1: a deployment-related 5xx spike. Simulate checkout-service, payment-service, inventory-service, and a database in one repository; do not create real microservices or an AI agent.

Provide modular service definitions, topology, logs, metrics, deployment history, runbooks, incident metadata, and isolated evaluation ground truth. Use consistent extensible schemas suitable for future search_logs(), get_metrics(), get_recent_deployments(), get_dependencies(), and search_runbooks() tools.

Scenario: checkout-service v2.3.1 is deployed, a new NullPointerException appears shortly afterward, checkout 5xx rises significantly, and an alert fires. Ground truth: checkout-service deployment regression, affected service checkout-service, SEV-2.

Add useful validation/tests and README documentation covering architecture, scenario, topology, and this prompts directory. Report final structure, key files, checks, assumptions, and changed files. Do not implement other use cases or the AI agent. Do not push without approval. Save only concise implementation specifications here, not routine debugging conversations.
