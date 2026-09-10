from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from signal_trace.tools import (
    SyntheticSource, ToolDataError, ToolLayer, get_dependencies, get_metrics,
    get_recent_deployments, search_logs, search_runbooks,
)
from signal_trace.tools.models import SearchLogsInput

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 1, 15, 10, tzinfo=timezone.utc)
END = START + timedelta(minutes=15)
SERVICE = 'checkout-service'


class ToolTests(unittest.TestCase):
    def test_normalized_logs_and_service_filter(self):
        logs = search_logs(SERVICE, START, END)
        self.assertEqual(len(logs), 15)
        self.assertEqual({r.service for r in logs}, {SERVICE})
        self.assertEqual(logs[0].trace_id, 'trace-00')
        self.assertEqual(logs[0].log_id, 'log-checkout-service-00')
        self.assertNotIn('service_id', logs[0].model_dump())
        self.assertEqual(len(search_logs('payment-service', START, END)), 15)

    def test_log_time_boundaries(self):
        start = START + timedelta(seconds=15)
        logs = search_logs(SERVICE, start, start + timedelta(minutes=1))
        self.assertEqual([r.timestamp for r in logs], [start])
        self.assertEqual(search_logs(SERVICE, START, START), [])

    def test_level_and_keyword(self):
        errors = search_logs(SERVICE, START, END, level='ERROR', keyword='DISCOUNT')
        self.assertEqual(len(errors), 9)
        self.assertTrue(all(r.level == 'ERROR' for r in errors))
        self.assertEqual(search_logs(SERVICE, START, END, level='INFO', keyword='discount'), [])
        self.assertEqual(search_logs('payment-service', START, END, level='ERROR'), [])
        self.assertEqual(search_logs(SERVICE, START, END, keyword='not present'), [])

    def test_metric_normalization(self):
        rows = get_metrics(SERVICE, START, END)
        self.assertEqual(len(rows), 60)
        self.assertEqual({r.service for r in rows}, {SERVICE})
        self.assertEqual({r.metric_name for r in rows},
                         {'request_count', 'error_5xx_count', 'error_5xx_rate', 'latency_p95_ms'})
        self.assertEqual(rows, get_metrics(SERVICE, START, END, metric_name=None))
        self.assertEqual(get_metrics(SERVICE, START, END, metric_name='http_requests'), [])
        rates = get_metrics(SERVICE, START, END, metric_name='error_5xx_rate')
        self.assertEqual(len(rates), 15)
        self.assertEqual(rates[0].value, .001)
        self.assertEqual(rates[-1].value, .18)
        self.assertTrue(all(r.unit == 'fraction' and r.window_seconds == 60 for r in rates))
        self.assertEqual(rates[0].metric_id, 'metric-checkout-service-00')
        self.assertEqual(get_metrics(SERVICE, START, END, metric_name='unknown'), [])

    def test_metric_time_and_service_filter(self):
        rows = get_metrics('payment-service', START, START + timedelta(minutes=1), 'request_count')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].timestamp, rows[0].service, rows[0].value),
                         (START, 'payment-service', 1000))
        self.assertEqual(get_metrics(SERVICE, START, START), [])

    def test_deployments_since_inclusive_and_order(self):
        rows = get_recent_deployments(SERVICE, START + timedelta(minutes=5))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].version, '2.3.1')
        self.assertEqual(rows[0].metadata.from_version, '2.3.0')
        self.assertEqual(rows[0].metadata.status, 'succeeded')
        self.assertEqual(rows[0].timestamp, START + timedelta(minutes=5))
        self.assertEqual(rows[0].service, SERVICE)
        history = get_recent_deployments(SERVICE, START - timedelta(days=2))
        self.assertEqual([r.version for r in history], ['2.3.1', '2.3.0'])
        self.assertEqual(get_recent_deployments('payment-service', START), [])
        self.assertEqual(get_recent_deployments(SERVICE, END), [])

    def test_dependencies_direct_and_upstream(self):
        checkout = get_dependencies(SERVICE)
        self.assertTrue(checkout.service_known)
        self.assertEqual(checkout.downstream, ['database', 'inventory-service', 'payment-service'])
        self.assertEqual(checkout.upstream, [])
        payment = get_dependencies('payment-service')
        self.assertEqual(payment.downstream, ['database'])
        self.assertEqual(payment.upstream, [SERVICE])
        database = get_dependencies('database')
        self.assertTrue(database.service_known)
        self.assertEqual(database.downstream, [])
        self.assertEqual(database.upstream, [SERVICE, 'inventory-service', 'payment-service'])

    def test_runbook_retrieval(self):
        books = search_runbooks('DEPLOYMENT 5xx', service=SERVICE)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].runbook_id, 'rb-deployment-5xx')
        self.assertIn(SERVICE, books[0].services)
        self.assertTrue(books[0].steps)
        self.assertEqual(books[0].source, 'runbook:rb-deployment-5xx')
        self.assertEqual(search_runbooks('deployment 5xx'), books)
        self.assertEqual(search_runbooks('deployment absent'), [])
        self.assertEqual(search_runbooks('deployment', service='database'), [])
        self.assertEqual(search_runbooks('!!!'), [])

    def test_unknown_services(self):
        unknown = '../../evaluation'
        self.assertEqual(search_logs(unknown, START, END), [])
        self.assertEqual(get_metrics(unknown, START, END), [])
        self.assertEqual(get_recent_deployments(unknown, START), [])
        self.assertEqual(search_runbooks('deployment', unknown), [])
        dependencies = get_dependencies(unknown)
        self.assertFalse(dependencies.service_known)
        self.assertEqual(dependencies.downstream, [])
        self.assertEqual(dependencies.upstream, [])

    def test_empty_time_results(self):
        future = END + timedelta(days=1)
        self.assertEqual(search_logs(SERVICE, future, future + timedelta(hours=1)), [])
        self.assertEqual(get_metrics(SERVICE, future, future + timedelta(hours=1)), [])

    def test_invalid_times(self):
        for tool in (search_logs, get_metrics):
            for start, end in [(END, START), (START.replace(tzinfo=None), END),
                               (START, END.replace(tzinfo=None)), ('yesterday', END), (123, END)]:
                with self.subTest(tool=tool.__name__, start=start, end=end):
                    with self.assertRaises(ValidationError):
                        tool(SERVICE, start, end)
        with self.assertRaises(ValidationError):
            get_recent_deployments(SERVICE, START.replace(tzinfo=None))

    def test_timezone_normalization(self):
        offset = timezone(timedelta(hours=2))
        self.assertEqual(search_logs(SERVICE, START, END),
                         search_logs(SERVICE, START.astimezone(offset), END.astimezone(offset)))

    def test_invalid_parameters(self):
        calls = [lambda: search_logs('', START, END),
                 lambda: get_metrics(' ', START, END),
                 lambda: get_recent_deployments(123, START),
                 lambda: get_dependencies(''),
                 lambda: search_runbooks(' '),
                 lambda: search_runbooks('deployment', service=3),
                 lambda: search_logs(SERVICE, START, END, level='BOGUS'),
                 lambda: search_logs(SERVICE, START, END, keyword=''),
                 lambda: get_metrics(SERVICE, START, END, metric_name=1)]
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(ValidationError):
                    call()
        with self.assertRaises(ValidationError):
            SearchLogsInput(service=SERVICE, start_time=START, end_time=END, extra='invalid')

    def test_read_only_and_evaluation_isolation(self):
        original = Path.open
        accessed = []

        def guard(path, mode='r', *args, **kwargs):
            self.assertFalse(any(flag in mode for flag in 'wax+'))
            self.assertNotIn('evaluation', path.parts)
            accessed.append(path)
            return original(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', guard):
            search_logs(SERVICE, START, END)
            get_metrics(SERVICE, START, END)
            get_recent_deployments(SERVICE, START)
            get_dependencies(SERVICE)
            search_runbooks('deployment')
        self.assertTrue(accessed)

    def test_source_errors_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            layer = ToolLayer(SyntheticSource(Path(directory)))
            with self.assertRaises(ToolDataError):
                layer.search_logs(SERVICE, START, END)
        from signal_trace.tools import source
        original = source.read

        def corrupt(path):
            if path.name == 'logs.jsonl':
                return [{'timestamp': 'invalid'}]
            return original(path)

        with patch.object(source, 'read', corrupt):
            with self.assertRaises(ToolDataError):
                search_logs(SERVICE, START, END)

    def test_source_can_be_replaced(self):
        class EmptySource:
            def logs(self):
                return []
        self.assertEqual(ToolLayer(EmptySource()).search_logs(SERVICE, START, END), [])

    def test_results_are_independent_and_serializable(self):
        books = search_runbooks('deployment')
        books[0].steps.clear()
        self.assertTrue(search_runbooks('deployment')[0].steps)
        record = search_logs(SERVICE, START, END)[0]
        self.assertEqual(json.loads(record.model_dump_json())['service'], SERVICE)


if __name__ == '__main__':
    unittest.main()
