"""Read-only operational tools, also available through an injectable ToolLayer."""
from signal_trace.tools.service import ToolLayer
from signal_trace.tools.source import OperationalSource, SyntheticSource, ToolDataError

_default = ToolLayer()
search_logs = _default.search_logs
get_metrics = _default.get_metrics
get_recent_deployments = _default.get_recent_deployments
get_dependencies = _default.get_dependencies
search_runbooks = _default.search_runbooks

__all__ = [
    'ToolLayer', 'OperationalSource', 'SyntheticSource', 'ToolDataError',
    'search_logs', 'get_metrics', 'get_recent_deployments', 'get_dependencies', 'search_runbooks',
]
