"""Typed contracts independent of the synthetic storage format."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError('Must not be blank')
    return value


Text = Annotated[str, AfterValidator(nonblank)]
UTCTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(timezone.utc))]


class ToolModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ServiceInput(ToolModel):
    service: Text


class TimeRangeInput(ServiceInput):
    start_time: UTCTime
    end_time: UTCTime

    @model_validator(mode='after')
    def ordered(self) -> 'TimeRangeInput':
        if self.start_time > self.end_time:
            raise ValueError('start_time must not be after end_time')
        return self


class SearchLogsInput(TimeRangeInput):
    level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] | None = None
    keyword: Text | None = None


class GetMetricsInput(TimeRangeInput):
    metric_name: Text | None = None


class GetRecentDeploymentsInput(ServiceInput):
    since: UTCTime


class GetDependenciesInput(ServiceInput):
    pass


class SearchRunbooksInput(ToolModel):
    query: Text
    service: Text | None = None


class LogRecord(ToolModel):
    log_id: str
    timestamp: UTCTime
    service: str
    level: str
    message: str
    trace_id: str | None = None


class MetricRecord(ToolModel):
    metric_id: str
    timestamp: UTCTime
    service: str
    metric_name: str
    value: int | float
    unit: str | None = None
    window_seconds: int


class DeploymentMetadata(ToolModel):
    from_version: str
    status: str
    environment: str


class DeploymentRecord(ToolModel):
    deployment_id: str
    service: str
    version: str
    timestamp: UTCTime
    metadata: DeploymentMetadata | None = None


class Dependencies(ToolModel):
    service: str
    service_known: bool
    downstream: list[str] = Field(default_factory=list)
    upstream: list[str] = Field(default_factory=list)


class RunbookRecord(ToolModel):
    runbook_id: str
    title: str
    services: list[str]
    tags: list[str]
    symptoms: list[str]
    steps: list[str]
    source: str
