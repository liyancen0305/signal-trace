"""Isolated Agent execution: only operational JSON enters over stdin."""
import copy
import json
from pathlib import Path
import sys

from signal_trace.agent import Investigator, OfflineReferenceModel, OllamaModel
from signal_trace.models.incident import IncidentRequest
from signal_trace.tools import ToolLayer
from signal_trace.tools.models import LogRecord, MetricRecord, DeploymentRecord, RunbookRecord


class FixtureSource:
    def __init__(self, data):
        self.data = data

    def records(self, name, schema):
        fault = self.data.get('faults', {}).get(name)
        if fault == 'timeout':
            raise TimeoutError('Synthetic operational source timeout')
        return [schema.model_validate_json(json.dumps(row)) for row in self.data[name]]

    def logs(self):
        return self.records('logs', LogRecord)

    def metrics(self):
        return self.records('metrics', MetricRecord)

    def deployments(self):
        return self.records('deployments', DeploymentRecord)

    def runbooks(self):
        return self.records('runbooks', RunbookRecord)

    def services(self):
        return set(self.data['services'])

    def edges(self):
        return [tuple(edge) for edge in self.data['edges']]


class RecordingTools:
    """Independent ledger records tool returns before the Agent receives them."""
    def __init__(self, layer):
        self.layer, self.ledger = layer, []

    def __getattr__(self, name):
        original = getattr(self.layer, name)
        def invoke(**arguments):
            entry = {'tool': name, 'arguments': json.loads(json.dumps(arguments, default=str))}
            self.ledger.append(entry)
            try:
                result = original(**arguments)
                rows = result if isinstance(result, list) else ([] if result is None else [result])
                entry['records'] = [copy.deepcopy(row.model_dump(mode='json')) for row in rows]
                return result
            except Exception as exc:
                entry.update(error=type(exc).__name__, records=[])
                raise
        return invoke


def deny_evaluation_reads(root):
    """Deny Python filesystem access to labels and saved reports in the worker.

    An application isolation check, not a sandbox for hostile native code.
    """
    forbidden = [root / 'evaluation', root / 'reports']
    def audit(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes)):
            path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0]).resolve()
            if any(path == base or base in path.parents for base in forbidden):
                raise PermissionError('Evaluation data is inaccessible during Agent execution')
    sys.addaudithook(audit)


def main():
    payload = json.load(sys.stdin)
    root = Path(__file__).resolve().parents[1]
    deny_evaluation_reads(root)
    config = payload['config']
    model = OfflineReferenceModel() if config['provider'] == 'offline' else OllamaModel(
        config['model'], config['base_url'], timeout=config['model_timeout'])
    tools = RecordingTools(ToolLayer(FixtureSource(payload['operational'])))
    run = Investigator(model, tools, max_iterations=config['max_iterations'],
                       tool_timeout=config['tool_timeout'], model_timeout=config['model_timeout']).run(
                           IncidentRequest.model_validate(payload['incident']))
    print(json.dumps({'run': run.model_dump(mode='json'), 'ledger': tools.ledger}))


if __name__ == '__main__':
    main()
