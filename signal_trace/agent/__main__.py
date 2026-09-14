"""Read an incident from stdin; print a complete structured investigation run."""
import argparse
import sys

from signal_trace.agent import Investigator, OfflineReferenceModel, OllamaModel
from signal_trace.config import Settings
from signal_trace.models.incident import IncidentRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['offline', 'ollama'])
    parser.add_argument('--model')
    parser.add_argument('--max-iterations', type=int)
    args = parser.parse_args()
    settings = Settings()
    provider = args.provider or settings.agent_provider
    model = OfflineReferenceModel() if provider == 'offline' else OllamaModel(
        args.model or settings.agent_model or '', settings.agent_base_url)
    incident = IncidentRequest.model_validate_json(sys.stdin.read())
    run = Investigator(model, max_iterations=args.max_iterations if args.max_iterations is not None
                       else settings.agent_max_iterations).run(incident)
    print(run.model_dump_json(indent=2))


if __name__ == '__main__':
    main()
