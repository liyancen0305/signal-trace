"""Read-only adapter for the existing Use Case 1 fixtures; never reads evaluation."""
from pathlib import Path
from typing import Protocol
from datetime import datetime

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from signal_trace.tools.models import DeploymentMetadata, DeploymentRecord, LogRecord, MetricRecord, RunbookRecord
from signal_trace.validation import FORMATS, read


class OperationalSource(Protocol):
    """Normalized source interface for future operational system adapters."""

    def logs(self) -> list[LogRecord]: ...
    def metrics(self) -> list[MetricRecord]: ...
    def deployments(self) -> list[DeploymentRecord]: ...
    def services(self) -> set[str]: ...
    def edges(self) -> list[tuple[str, str]]: ...
    def runbooks(self) -> list[RunbookRecord]: ...


class ToolDataError(RuntimeError):
    """Operational data is unavailable or violates its existing schema."""


class SyntheticSource:
    def __init__(self, root: Path | None = None):
        self._root = Path(root) if root is not None else Path(__file__).resolve().parents[2]

    def _records(self, kind: str, pattern: str) -> list[dict]:
        try:
            schema = read(self._root / 'schemas' / f'{kind}.schema.json')[0]
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, format_checker=FORMATS)
            paths = sorted(self._root.glob(pattern))
            if not paths:
                raise ToolDataError(f'Missing {kind} data')
            rows = []
            for path in paths:
                records = read(path)
                if not records:
                    raise ToolDataError(f'Empty required {kind} data')
                for row in records:
                    if not validator.is_valid(row):
                        raise ToolDataError(f'Invalid {kind} data')
                    rows.append(row)
            return rows
        except (OSError, ValueError, SchemaError) as exc:
            raise ToolDataError(f'Cannot load {kind} data') from exc

    @staticmethod
    def _time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))

    def logs(self) -> list[LogRecord]:
        return [LogRecord(log_id=r['log_id'], timestamp=self._time(r['timestamp']),
                          service=r['service_id'], level=r['level'], message=r['message'],
                          trace_id=r.get('trace_id'))
                for r in self._records('log', 'scenarios/uc1_deployment_5xx/logs.jsonl')]

    def metrics(self) -> list[MetricRecord]:
        units = {'request_count': 'requests', 'error_5xx_count': 'requests',
                 'error_5xx_rate': 'fraction', 'latency_p95_ms': 'ms'}
        return [MetricRecord(metric_id=r['metric_id'], timestamp=self._time(r['timestamp']),
                             service=r['service_id'], metric_name=name, value=r[name], unit=unit,
                             window_seconds=r['window_seconds'])
                for r in self._records('metric', 'scenarios/uc1_deployment_5xx/metrics.jsonl')
                for name, unit in units.items()]

    def deployments(self) -> list[DeploymentRecord]:
        return [DeploymentRecord(deployment_id=r['deployment_id'], service=r['service_id'],
                                 version=r['to_version'], timestamp=self._time(r['timestamp']),
                                 metadata=DeploymentMetadata(from_version=r['from_version'],
                                                             status=r['status'], environment=r['environment']))
                for r in self._records('deployment', 'scenarios/uc1_deployment_5xx/deployments.jsonl')]

    def services(self) -> set[str]:
        return {r['service_id'] for r in self._records('service', 'environment/services/*.json')}

    def edges(self) -> list[tuple[str, str]]:
        return [(edge['source'], edge['target'])
                for edge in self._records('topology', 'environment/topology.json')[0]['edges']]

    def runbooks(self) -> list[RunbookRecord]:
        return [RunbookRecord(runbook_id=r['runbook_id'], title=r['title'], services=r['service_ids'],
                              tags=r['tags'], symptoms=r['symptoms'], steps=r['steps'],
                              source=f"runbook:{r['runbook_id']}")
                for r in self._records('runbook', 'runbooks/*.json')]
