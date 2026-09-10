"""Deterministic tools over normalized operational records."""
from datetime import datetime
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signal_trace.retrieval.service import Retriever

from signal_trace.tools.models import (
    Dependencies, DeploymentRecord, GetDependenciesInput, GetMetricsInput,
    GetRecentDeploymentsInput, LogRecord, MetricRecord, RunbookRecord,
    SearchLogsInput, SearchRunbooksInput,
)
from signal_trace.tools.source import OperationalSource, SyntheticSource


class ToolLayer:
    def __init__(self, source: OperationalSource | None = None, retriever: "Retriever | None" = None):
        self._source = source if source is not None else SyntheticSource()
        self._retriever = retriever

    def search_logs(self, service: str, start_time: datetime, end_time: datetime,
                    level: str | None = None, keyword: str | None = None) -> list[LogRecord]:
        query = SearchLogsInput(service=service, start_time=start_time, end_time=end_time,
                                level=level, keyword=keyword)
        return sorted((r for r in self._source.logs()
                       if r.service == query.service and query.start_time <= r.timestamp < query.end_time
                       and (query.level is None or r.level == query.level)
                       and (query.keyword is None or query.keyword.casefold() in r.message.casefold())),
                      key=lambda r: (r.timestamp, r.log_id))

    def get_metrics(self, service: str, start_time: datetime, end_time: datetime,
                    metric_name: str | None = None) -> list[MetricRecord]:
        query = GetMetricsInput(service=service, start_time=start_time, end_time=end_time,
                                metric_name=metric_name)
        return sorted((r for r in self._source.metrics()
                       if r.service == query.service and query.start_time <= r.timestamp < query.end_time
                       and (query.metric_name is None or r.metric_name == query.metric_name)),
                      key=lambda r: (r.timestamp, r.metric_name, r.metric_id))

    def get_recent_deployments(self, service: str, since: datetime) -> list[DeploymentRecord]:
        query = GetRecentDeploymentsInput(service=service, since=since)
        return sorted((r for r in self._source.deployments()
                       if r.service == query.service and r.timestamp >= query.since),
                      key=lambda r: (r.timestamp, r.deployment_id), reverse=True)

    def get_dependencies(self, service: str) -> Dependencies:
        query = GetDependenciesInput(service=service)
        if query.service not in self._source.services():
            return Dependencies(service=query.service, service_known=False)
        edges = self._source.edges()
        return Dependencies(service=query.service, service_known=True,
                            downstream=sorted({target for caller, target in edges if caller == query.service}),
                            upstream=sorted({caller for caller, target in edges if target == query.service}))

    def search_runbooks(self, query: str, service: str | None = None, top_k: int = 5) -> list[RunbookRecord]:
        request = SearchRunbooksInput(query=query, service=service, top_k=top_k)
        retriever = self._retriever
        if retriever is None:
            from signal_trace.config import Settings
            settings = Settings()
            if settings.retrieval_database_url is not None:
                from signal_trace.retrieval.runtime import configured_retriever
                retriever = configured_retriever(settings.retrieval_database_url.get_secret_value(),
                                                settings.embedding_model, settings.embedding_cache_dir,
                                                settings.retrieval_min_score)
        if retriever is not None:
            results = retriever.search(request.query, request.service, request.top_k)
            return [RunbookRecord.model_validate({
                **result.metadata['runbook'], 'chunk_id': result.chunk_id,
                'text': result.text, 'score': result.score, 'metadata': result.metadata,
            }) for result in results]
        tokens = set(re.findall(r'\w+', request.query.casefold()))
        if not tokens:
            return []
        matches = []
        for book in self._source.runbooks():
            if request.service is not None and request.service not in book.services:
                continue
            content = ' '.join([book.title, *book.tags, *book.symptoms, *book.steps])
            words = set(re.findall(r'\w+', content.casefold()))
            if tokens <= words:
                matches.append(book)
        return sorted(matches, key=lambda book: book.runbook_id)[:request.top_k]
