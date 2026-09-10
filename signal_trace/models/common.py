"""Shared API model constraints."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
