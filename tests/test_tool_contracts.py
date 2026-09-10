"""Contract and failure-path audit for every public Part 3 tool."""
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from signal_trace.tools import ToolLayer, SyntheticSource, ToolDataError
from signal_trace.tools.models import (
    SearchLogsInput, GetMetricsInput, GetRecentDeploymentsInput,
    GetDependenciesInput, SearchRunbooksInput,
)
from test_tools import START, END, SERVICE, ROOT


class ToolContractTests(unittest.TestCase):
    def setUp(self):
        self.tools = ToolLayer()
        self.calls = [
            ('search_logs', {'service': SERVICE, 'start_time': START, 'end_time': END}, SearchLogsInput),
            ('get_metrics', {'service': SERVICE, 'start_time': START, 'end_time': END}, GetMetricsInput),
            ('get_recent_deployments', {'service': SERVICE, 'since': START}, GetRecentDeploymentsInput),
            ('get_dependencies', {'service': SERVICE}, GetDependenciesInput),
            ('search_runbooks', {'query': 'deployment', 'service': SERVICE}, SearchRunbooksInput),
        ]

    def test_all_input_and_output_json_schemas_and_roundtrips(self):
        for name, kwargs, model in self.calls:
            with self.subTest(tool=name):
                query = model(**kwargs)
                for record in [query]:
                    schema = type(record).model_json_schema()
                    Draft202012Validator.check_schema(schema)
                    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                        json.loads(record.model_dump_json()))
                    self.assertEqual(type(record).model_validate_json(record.model_dump_json()), record)
                result = getattr(self.tools, name)(**kwargs)
                records = result if isinstance(result, list) else [result]
                self.assertTrue(records)
                for record in records:
                    schema = type(record).model_json_schema()
                    Draft202012Validator.check_schema(schema)
                    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                        json.loads(record.model_dump_json()))
                    self.assertEqual(type(record).model_validate_json(record.model_dump_json()), record)
                with self.assertRaises(ValidationError):
                    model(**kwargs, unexpected=True)

    def test_invalid_services_for_every_tool_before_io(self):
        for name, kwargs, _ in self.calls:
            for invalid in ('', '  ', 42, [], {}):
                with self.subTest(tool=name, invalid=invalid):
                    with patch.object(Path, 'open', side_effect=AssertionError('Input must fail before IO')):
                        with self.assertRaises(ValidationError):
                            getattr(self.tools, name)(**{**kwargs, 'service': invalid})

    def test_invalid_optional_parameters_and_since(self):
        for name, kwargs, field, values in [
            ('search_logs', self.calls[0][1], 'keyword', [42, [], '  ']),
            ('search_logs', self.calls[0][1], 'level', [42, [], '', 'error']),
            ('get_metrics', self.calls[1][1], 'metric_name', [False, [], '', '  ']),
            ('get_recent_deployments', self.calls[2][1], 'since', [None, '', 0, START.replace(tzinfo=None)]),
            ('search_runbooks', self.calls[4][1], 'query', [None, '', '  ', 42, []]),
        ]:
            for invalid in values:
                with self.subTest(tool=name, field=field, invalid=invalid):
                    with self.assertRaises(ValidationError):
                        getattr(self.tools, name)(**{**kwargs, field: invalid})

    def test_all_metric_names_and_units(self):
        for name, value, unit in [('request_count', 1000, 'requests'),
                                  ('error_5xx_count', 1, 'requests'),
                                  ('error_5xx_rate', .001, 'fraction'),
                                  ('latency_p95_ms', 90, 'ms')]:
            with self.subTest(metric=name):
                rows = self.tools.get_metrics(SERVICE, START, START + timedelta(minutes=1), name)
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0].metric_name, rows[0].value, rows[0].unit), (name, value, unit))

    def test_offset_times_and_deployment_exclusion(self):
        offset = timezone(timedelta(hours=-6))
        self.assertEqual(self.tools.get_metrics(SERVICE, START, END),
                         self.tools.get_metrics(SERVICE, START.astimezone(offset), END.astimezone(offset)))
        since = START + timedelta(minutes=5)
        self.assertEqual(self.tools.get_recent_deployments(SERVICE, since),
                         self.tools.get_recent_deployments(SERVICE, since.astimezone(offset)))
        self.assertEqual(self.tools.get_recent_deployments(SERVICE, since + timedelta(microseconds=1)), [])

    def test_runbooks_match_content_and_all_applicable_services(self):
        for query in ('REGRESSION', 'Elevated HTTP', 'normal approval process', 'deployment, 5xx!'):
            for service in (SERVICE, 'payment-service', 'inventory-service'):
                with self.subTest(query=query, service=service):
                    self.assertEqual(len(self.tools.search_runbooks(query, service)), 1)
        self.assertEqual(self.tools.search_runbooks('deploy'), [])  # whole-word retrieval

    def test_all_tools_work_with_normalized_memory_source_without_files(self):
        source = SyntheticSource()
        memory = SimpleNamespace(**{
            name: (lambda values=getattr(source, name)(): values)
            for name in ('logs', 'metrics', 'deployments', 'services', 'edges', 'runbooks')
        })
        tools = ToolLayer(memory)
        expected = {name: getattr(self.tools, name)(**kwargs) for name, kwargs, _ in self.calls}
        with patch.object(Path, 'open', side_effect=AssertionError('No files allowed')):
            for name, kwargs, _ in self.calls:
                with self.subTest(tool=name):
                    self.assertEqual(getattr(tools, name)(**kwargs), expected[name])

    def test_empty_source_and_isolated_known_service(self):
        memory = SimpleNamespace(**{name: lambda: [] for name in
                                   ('logs', 'metrics', 'deployments', 'edges', 'runbooks')},
                                 services=lambda: {SERVICE})
        tools = ToolLayer(memory)
        for name, kwargs, _ in self.calls:
            with self.subTest(tool=name):
                result = getattr(tools, name)(**kwargs)
                if name == 'get_dependencies':
                    self.assertTrue(result.service_known)
                    self.assertEqual(result.downstream, [])
                    self.assertEqual(result.upstream, [])
                else:
                    self.assertEqual(result, [])

    def test_fixtures_unchanged_after_all_calls(self):
        paths = [p for folder in ('environment', 'scenarios', 'runbooks', 'schemas', 'evaluation')
                 for p in (ROOT / folder).rglob('*') if p.is_file()]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        for name, kwargs, _ in self.calls:
            getattr(self.tools, name)(**kwargs)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_missing_data_for_every_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = ToolLayer(SyntheticSource(Path(directory)))
            for name, kwargs, _ in self.calls:
                with self.subTest(tool=name):
                    with self.assertRaises(ToolDataError):
                        getattr(tools, name)(**kwargs)

    def test_empty_required_fixture_is_data_error(self):
        from signal_trace.tools import source
        original = source.read
        def empty_logs(path):
            return [] if path.name == 'logs.jsonl' else original(path)
        with patch.object(source, 'read', empty_logs):
            with self.assertRaises(ToolDataError):
                self.tools.search_logs(SERVICE, START, END)

    def test_invalid_source_schema_is_data_error(self):
        from signal_trace.tools import source
        original = source.read
        def invalid_schema(path):
            return [{'type': 'not-a-json-schema-type'}] if path.name == 'log.schema.json' else original(path)
        with patch.object(source, 'read', invalid_schema):
            with self.assertRaises(ToolDataError):
                self.tools.search_logs(SERVICE, START, END)

    def test_malformed_data_for_every_tool(self):
        from signal_trace.tools import source
        original = source.read
        filenames = ['logs.jsonl', 'metrics.jsonl', 'deployments.jsonl',
                     'topology.json', 'deployment_5xx.json']
        for (name, kwargs, _), filename in zip(self.calls, filenames):
            def corrupt(path):
                return [{'invalid': True}] if path.name == filename else original(path)
            with self.subTest(tool=name):
                with patch.object(source, 'read', corrupt):
                    with self.assertRaises(ToolDataError):
                        getattr(self.tools, name)(**kwargs)

    def test_malformed_json_is_data_error(self):
        from signal_trace.tools import source
        original = source.read
        def malformed(path):
            if path.name == 'logs.jsonl':
                raise json.JSONDecodeError('Malformed fixture', '{', 0)
            return original(path)
        with patch.object(source, 'read', malformed):
            with self.assertRaises(ToolDataError):
                self.tools.search_logs(SERVICE, START, END)
