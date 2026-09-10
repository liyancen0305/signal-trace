"""Validate shared contracts and cross-record consistency from a repository root."""
import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


FORMATS = FormatChecker()


@FORMATS.checks('date-time', raises=ValueError)
def valid_utc_timestamp(value):
    if not isinstance(value, str):
        return True  # Type errors belong to the schema's type validator.
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', value):
        return False
    datetime.fromisoformat(value.replace('Z', '+00:00'))
    return True


def read(path):
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in path.read_text().splitlines()]
    return [json.loads(path.read_text())]


def validate(root):
    root = Path(root)
    errors = []
    records = {}
    patterns = {
        'service': 'environment/services/*.json',
        'topology': 'environment/topology.json',
        'runbook': 'runbooks/*.json',
        'incident': 'scenarios/*/incident.json',
        'log': 'scenarios/*/logs.jsonl',
        'metric': 'scenarios/*/metrics.jsonl',
        'deployment': 'scenarios/*/deployments.jsonl',
        'ground_truth': 'evaluation/*.json',
    }
    for kind, pattern in patterns.items():
        schema = json.loads((root / 'schemas' / f'{kind}.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FORMATS)
        paths = sorted(root.glob(pattern))
        if not paths:
            errors.append(f'Missing {kind} fixtures')
        records[kind] = []
        for path in paths:
            try:
                rows = read(path)
            except (ValueError, OSError) as exc:
                errors.append(f'{path}: {exc}')
                continue
            if not rows:
                errors.append(f'{path}: empty fixture')
            for row in rows:
                for error in validator.iter_errors(row):
                    errors.append(f'{path}: {error.json_path}: {error.message}')
            records[kind].extend(rows)
    if errors:
        return errors

    def check(condition, message):
        if not condition:
            errors.append(message)

    def timestamp(value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        check(parsed.utcoffset() == timedelta(0), f'Timestamp must be UTC: {value}')
        return parsed

    services = {x['service_id'] for x in records['service']}
    for kind, key in [('service', 'service_id'), ('runbook', 'runbook_id'),
                      ('incident', 'incident_id'), ('ground_truth', 'incident_id'),
                      ('log', 'log_id'), ('metric', 'metric_id'), ('deployment', 'deployment_id')]:
        ids = [x[key] for x in records[kind]]
        check(len(ids) == len(set(ids)), f'Duplicate {kind} IDs')
    for edge in records['topology'][0]['edges']:
        check(edge['source'] in services and edge['target'] in services, 'Unknown topology service')
    for book in records['runbook']:
        check(set(book['service_ids']) <= services, 'Unknown runbook service')
    for kind in ['log', 'metric', 'deployment']:
        for row in records[kind]:
            check(row['service_id'] in services, f'Unknown {kind} service')
            timestamp(row['timestamp'])
    for metric in records['metric']:
        requests, failures = metric['request_count'], metric['error_5xx_count']
        check(failures <= requests, '5xx count exceeds requests')
        expected = failures / requests if requests else 0
        check(abs(metric['error_5xx_rate'] - expected) < 1e-9, '5xx rate does not match counts')

    truths = {x['incident_id']: x for x in records['ground_truth']}
    check(set(truths) == {x['incident_id'] for x in records['incident']}, 'Incident/ground truth mismatch')
    for folder in sorted((root / 'scenarios').iterdir()):
        if not folder.is_dir():
            continue
        required = ['incident.json', 'logs.jsonl', 'metrics.jsonl', 'deployments.jsonl']
        if not all((folder / file).is_file() for file in required):
            errors.append(f'{folder}: missing required scenario fixture')
            continue
        incident = read(folder / 'incident.json')[0]
        logs, metrics, deployments = [read(folder / file) for file in required[1:]]
        start, end = timestamp(incident['observation_start']), timestamp(incident['observation_end'])
        alert = incident['alert']
        fired = timestamp(alert['timestamp'])
        check(start < end and start <= fired <= end, 'Invalid observation/alert interval')
        check(alert['service_id'] in services, 'Unknown alert service')
        for row in logs + metrics:
            t = timestamp(row['timestamp'])
            check(start <= t < end, 'Evidence outside observation interval')
            if 'window_seconds' in row:
                check(t + timedelta(seconds=row['window_seconds']) <= end, 'Metric window exceeds interval')
        lookup = {}
        for metric in metrics:
            key = (metric['service_id'], metric['name'], metric['timestamp'])
            check(key not in lookup, 'Duplicate metric window')
            lookup[key] = metric
        # Alert evaluates completed windows; metric timestamps mark window starts.
        for offset in range(1, alert['consecutive_windows'] + 1):
            t = (fired - timedelta(minutes=offset)).strftime('%Y-%m-%dT%H:%M:%SZ')
            metric = lookup.get((alert['service_id'], alert['metric_name'], t))
            check(metric is not None and metric['error_5xx_rate'] > alert['threshold'],
                  'Alert lacks consecutive elevated completed windows')
        truth = truths.get(incident['incident_id'])
        if truth:
            check(truth['affected_service'] in services, 'Unknown affected service')
            evidence = {x[key] for rows, key in [(logs, 'log_id'), (metrics, 'metric_id'),
                                                (deployments, 'deployment_id')] for x in rows}
            check(set(truth['evidence_ids']) <= evidence, 'Unknown ground truth evidence')
            check(any(x['deployment_id'] == truth['deployment_id'] for x in deployments),
                  'Unknown ground truth deployment')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = validate(args.root)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print('All synthetic environment schemas and consistency checks passed.')


if __name__ == '__main__':
    main()
