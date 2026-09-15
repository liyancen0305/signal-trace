"""Run Agent first, then load labels and score. Never imported by runtime."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from evaluation_harness.scoring import SCORER_VERSION, aggregate, score_case

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = dict(provider='offline', model='', base_url='http://localhost:11434',
                      max_iterations=12, tool_timeout=10, model_timeout=120, retrieval='fixture')


def execute(incident, operational, config):
    env = {k: v for k, v in os.environ.items() if not k.startswith('SIGNAL_TRACE_')}
    if config['retrieval'] == 'semantic':
        env.update({k: v for k, v in os.environ.items() if k.startswith('SIGNAL_TRACE_RETRIEVAL_') or k.startswith('SIGNAL_TRACE_EMBEDDING_')})
        if not env.get('SIGNAL_TRACE_RETRIEVAL_DATABASE_URL'):
            raise ValueError('Semantic mode requires SIGNAL_TRACE_RETRIEVAL_DATABASE_URL with an ingested corpus')
    timeout = (config['max_iterations'] + 2) * (4 * config['model_timeout'] + 2 * config['tool_timeout']) + 30
    process = subprocess.run([sys.executable, '-m', 'evaluation_harness.worker'],
        input=json.dumps({'incident': incident, 'operational': operational, 'config': config}),
        text=True, capture_output=True, cwd=ROOT, env=env, timeout=timeout)
    if process.returncode:
        raise RuntimeError('Agent worker failed: ' + process.stderr[-2000:])
    return json.loads(process.stdout)


def execution_failure(case_id, truth, error):
    return {'case_id': case_id, 'scenario': truth['scenario'], 'expected': truth,
            'actual': None, 'confidence': None, 'tool_sequence': [], 'execution_error': error,
            'scores': dict(root_cause=False, affected_services=False, severity=False, important_evidence=False, outcome=False),
            'grounding': {'pass': False, 'reference_errors': ['No valid execution'], 'unsupported_claims': []},
            'unsupported_claims': [], 'missing_important_evidence': truth['important_evidence'],
            'tool_usage': {'pass': False, 'decision_count': 0, 'repeated_calls': 0},
            'stopping': {'pass': False, 'reason': 'execution_failure'},
            'confidence_quality': {'pass': False, 'high_confidence_wrong': False, 'high_confidence_missing': False},
            'reliability': {'applicable': truth['reliability_case'], 'pass': False if truth['reliability_case'] else None},
            'overall_pass': False}


def run_suite(output, config=None, root=ROOT, executor=execute):
    config = {**DEFAULT_CONFIG, **(config or {})}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cases, hashes = [], {}
    # Discover operational IDs; do not read the label manifest before execution.
    for directory in sorted((root / 'fixtures/agent_scenarios').iterdir()):
        if not directory.is_dir():
            continue
        incident_path, data_path = directory / 'incident.json', directory / 'operational.json'
        incident, operational = json.loads(incident_path.read_text()), json.loads(data_path.read_text())
        execution, error = None, None
        try:
            execution = executor(incident, operational, config)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired, OSError) as exc:
            error = str(exc)
        (output / (directory.name + '.trace.json')).write_text(json.dumps(execution or {'error': error}, indent=2) + '\n')
        # Only now is this case's ground truth opened.
        truth_path = root / 'evaluation/agent' / (directory.name + '.json')
        truth = json.loads(truth_path.read_text())
        hashes[directory.name] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (incident_path, data_path, truth_path)}
        try:
            report = score_case(directory.name, truth, execution) if execution else execution_failure(directory.name, truth, error)
        except (KeyError, ValueError, TypeError) as exc:
            report = execution_failure(directory.name, truth, 'Malformed execution: ' + str(exc))
        cases.append(report)
        (output / (directory.name + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    manifest = json.loads((root / 'evaluation/agent/suite.json').read_text())
    if set(manifest['cases']) != {c['case_id'] for c in cases}:
        raise ValueError('Suite manifest and operational cases differ')
    report = {'schema_version': '1.0', 'scorer_version': SCORER_VERSION,
              'created_at': datetime.now(timezone.utc).isoformat(), 'config': config,
              'fixture_hashes': hashes,
              'implementation_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for folder in ('signal_trace', 'evaluation_harness') for p in sorted((ROOT / folder).rglob('*.py'))},
              'python_version': sys.version, 'llm_judge': None, 'aggregate': aggregate(cases), 'cases': cases}
    (output / 'aggregate.json').write_text(json.dumps(report, indent=2) + '\n')
    write_markdown(output, report)
    return report


def write_markdown(output, report):
    lines = ['# Agent evaluation report', '', 'Deterministic scoring only; confidence is not a probability.', '',
             '| Scenario | Root | Services | Severity | Grounding | Confidence | Overall |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for case in report['cases']:
        s = case['scores']
        lines.append(f'| {case["scenario"]} | {s["root_cause"]} | {s["affected_services"]} | {s["severity"]} | {case["grounding"]["pass"]} | {case["confidence"]} | {case["overall_pass"]} |')
    lines += ['', '## Aggregate', '', '```json', json.dumps(report['aggregate'], indent=2), '```']
    for case in report['cases']:
        lines += ['', '## ' + case['scenario'], '',
                  f'[Per-case metrics]({case["case_id"]}.json) · [Full trace and independent tool ledger]({case["case_id"]}.trace.json)', '',
                  '**Expected:** ' + case['expected']['root_cause'], '',
                  '**Actual:** ' + str(case['actual']['root_cause'] if case['actual'] else case.get('execution_error')), '',
                  '**Tools:** ' + ' → '.join(c['name'] + '(' + str(c['arguments'].get('service', '')) + ')' for c in case['tool_sequence']), '',
                  '```json', json.dumps({k: case[k] for k in ('scores', 'missing_important_evidence', 'unsupported_claims', 'tool_usage', 'stopping', 'confidence_quality', 'reliability')}, indent=2), '```']
    (output / 'report.md').write_text('\n'.join(lines) + '\n')


def compare_baseline(report, baseline):
    previous = {c['case_id']: c for c in baseline['cases']}
    regressions = []
    for case in report['cases']:
        old = previous.get(case['case_id'])
        if old is None:
            continue
        for metric, passed in old['scores'].items():
            if passed and not case['scores'].get(metric):
                regressions.append(case['case_id'] + ': ' + metric)
        for metric in ('grounding', 'tool_usage', 'stopping', 'confidence_quality', 'reliability'):
            if old[metric]['pass'] is True and case[metric]['pass'] is not True:
                regressions.append(case['case_id'] + ': ' + metric)
        if len(case['unsupported_claims']) > len(old['unsupported_claims']):
            regressions.append(case['case_id'] + ': increased unsupported claims')
    missing = sorted(set(previous) - {c['case_id'] for c in report['cases']})
    return {'regressions': regressions, 'missing_cases': missing, 'pass': not regressions and not missing,
            'aggregate_deltas': {key: value - baseline['aggregate'][key]
                for key, value in report['aggregate'].items()
                if isinstance(value, (int, float)) and isinstance(baseline['aggregate'].get(key), (int, float))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/part7')
    parser.add_argument('--provider', choices=['offline', 'ollama'], default='offline')
    parser.add_argument('--model', default='')
    parser.add_argument('--base-url', default=DEFAULT_CONFIG['base_url'])
    parser.add_argument('--retrieval', choices=['fixture', 'semantic'], default='fixture')
    parser.add_argument('--max-iterations', type=int, default=12)
    parser.add_argument('--tool-timeout', type=float, default=10)
    parser.add_argument('--model-timeout', type=float, default=120)
    parser.add_argument('--baseline', type=Path, help='Prior aggregate.json; exit 1 on newly regressed checks')
    parser.add_argument('--fail-on-quality', action='store_true', help='Exit 1 if any case fails quality scoring')
    args = vars(parser.parse_args())
    output, fail, baseline = args.pop('output'), args.pop('fail_on_quality'), args.pop('baseline')
    if baseline and baseline.resolve() == (output / 'aggregate.json').resolve():
        parser.error('Use a separate output directory when comparing a saved aggregate report')
    if args['provider'] == 'ollama' and not args['model']:
        parser.error('--model is required with --provider ollama')
    report = run_suite(output, args)
    print(json.dumps(report['aggregate'], indent=2))
    if baseline:
        comparison = compare_baseline(report, json.loads(baseline.read_text()))
        (output / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
        print(json.dumps(comparison, indent=2))
        if not comparison['pass']:
            raise SystemExit(1)
    if fail and not all(c['overall_pass'] for c in report['cases']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

