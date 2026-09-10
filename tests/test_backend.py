import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from signal_trace.config import Settings
from signal_trace.api.app import create_app
from signal_trace.models.incident import IncidentRequest
from signal_trace.models.responses import TriageResponse
from signal_trace.validation import FORMATS

ROOT = Path(__file__).resolve().parents[1]


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(TestClient(create_app(Settings())))
        self.incident = json.loads(
            (ROOT / "scenarios/uc1_deployment_5xx/incident.json").read_text()
        )

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_accepts_existing_incident(self):
        response = self.client.post("/incidents", json=self.incident)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "incident_id": self.incident["incident_id"],
            "status": "accepted",
            "message": "Incident accepted. Triage is not implemented.",
        })
        schema = json.loads((ROOT / "schemas/incident.schema.json").read_text())
        request = IncidentRequest.model_validate(self.incident)
        Draft202012Validator(schema, format_checker=FORMATS).validate(request.model_dump())

    def test_missing_required_fields(self):
        for key in self.incident:
            with self.subTest(field=key):
                payload = dict(self.incident)
                del payload[key]
                self.assertEqual(self.client.post("/incidents", json=payload).status_code, 422)
        for key in self.incident["alert"]:
            with self.subTest(alert_field=key):
                alert = dict(self.incident["alert"])
                del alert[key]
                payload = {**self.incident, "alert": alert}
                self.assertEqual(self.client.post("/incidents", json=payload).status_code, 422)

    def test_invalid_incident_fields(self):
        for field, value in [
            ("schema_version", "2.0"), ("incident_id", ""),
            ("incident_id", 123), ("title", ""), ("alert", []),
            ("unexpected", "value"), ("observation_start", "yesterday"),
            ("observation_start", "2026-02-30T10:00:00Z"),
            ("observation_start", "2026-01-15T10:00:00"),
            ("observation_start", "2026-01-15T10:00:00+01:00"),
            ("observation_start", 1768471200),
            ("observation_end", "2026-01-15T10:00:00Z"),
            ("observation_end", "2026-01-15T09:00:00Z"),
        ]:
            with self.subTest(field=field, value=value):
                payload = {**self.incident, field: value}
                self.assertEqual(self.client.post("/incidents", json=payload).status_code, 422)

    def test_invalid_alert_fields(self):
        for field, value in [
            ("threshold", -0.1), ("threshold", 1.1), ("threshold", "0.05"),
            ("threshold", True), ("consecutive_windows", 0),
            ("consecutive_windows", 1.5), ("consecutive_windows", True),
            ("service_id", ""), ("metric_name", "latency"),
            ("condition", "unknown"), ("unexpected", "value"),
            ("timestamp", "yesterday"),
            ("timestamp", "2026-01-15T09:59:59Z"),
            ("timestamp", "2026-01-15T10:15:01Z"),
        ]:
            with self.subTest(field=field, value=value):
                payload = {**self.incident, "alert": {**self.incident["alert"], field: value}}
                self.assertEqual(self.client.post("/incidents", json=payload).status_code, 422)

    def test_accepts_interval_and_threshold_boundaries(self):
        for timestamp in (self.incident["observation_start"], self.incident["observation_end"]):
            for threshold in (0, 1):
                with self.subTest(timestamp=timestamp, threshold=threshold):
                    alert = {**self.incident["alert"], "timestamp": timestamp, "threshold": threshold}
                    payload = {**self.incident, "alert": alert}
                    self.assertEqual(self.client.post("/incidents", json=payload).status_code, 200)

    def test_rejects_malformed_or_missing_body(self):
        self.assertEqual(self.client.post("/incidents").status_code, 422)
        self.assertEqual(self.client.post("/incidents", json=[]).status_code, 422)
        response = self.client.post(
            "/incidents", content="{", headers={"content-type": "application/json"}
        )
        self.assertEqual(response.status_code, 422)

    def test_acceptance_does_not_load_evidence_or_evaluation(self):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("Unexpected file read")):
            self.assertEqual(self.client.post("/incidents", json=self.incident).status_code, 200)

    def test_configuration_from_environment(self):
        with patch.dict("os.environ", {"SIGNAL_TRACE_APP_NAME": "Test Signal Trace"}):
            application = create_app()
        self.assertEqual(application.title, "Test Signal Trace")

    def test_openapi_exposes_request_and_acknowledgement(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("requestBody", schema["paths"]["/incidents"]["post"])
        self.assertIn("IncidentRequest", schema["components"]["schemas"])
        self.assertIn("IncidentAcknowledgement", schema["components"]["schemas"])

    def test_future_triage_response_contract(self):
        response = TriageResponse(
            incident_id=self.incident["incident_id"],
            summary="Insufficient evidence to identify a cause.",
            evidence_ids=[],
        )
        self.assertIsNone(response.root_cause)
        schema = TriageResponse.model_json_schema()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(response.model_dump())
        with self.assertRaises(ValidationError):
            TriageResponse(incident_id="incident", summary="summary", evidence_ids=[123])


if __name__ == "__main__":
    unittest.main()
