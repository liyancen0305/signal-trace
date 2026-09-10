"""Request fields mirror schemas/incident.schema.json (version 1.0)."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from signal_trace.models.common import APIModel, NonEmptyString
from signal_trace.validation import valid_utc_timestamp


def validate_timestamp(value: str) -> str:
    if not valid_utc_timestamp(value):
        raise ValueError("Timestamp must be a UTC date-time ending in Z")
    return value


UTCTimestamp = Annotated[
    str, AfterValidator(validate_timestamp), Field(json_schema_extra={"format": "date-time"})
]


class IncidentAlert(APIModel):
    timestamp: UTCTimestamp
    service_id: NonEmptyString
    metric_name: Literal["http_requests"]
    condition: Literal["error_5xx_rate > threshold for consecutive windows"]
    threshold: float = Field(ge=0, le=1)
    consecutive_windows: int = Field(ge=1)


class IncidentRequest(APIModel):
    schema_version: Literal["1.0"]
    incident_id: NonEmptyString
    title: NonEmptyString
    observation_start: UTCTimestamp
    observation_end: UTCTimestamp
    alert: IncidentAlert

    @model_validator(mode="after")
    def validate_interval(self) -> "IncidentRequest":
        start, end, fired = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in (self.observation_start, self.observation_end, self.alert.timestamp)
        )
        if not start < end or not start <= fired <= end:
            raise ValueError("Invalid observation/alert interval")
        return self
