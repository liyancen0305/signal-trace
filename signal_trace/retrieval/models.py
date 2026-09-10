"""Storage-independent knowledge and semantic search contracts."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RetrievalError(RuntimeError):
    """Knowledge retrieval or ingestion is unavailable."""


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SearchRequest(Model):
    query: Text
    service: Text | None = None
    top_k: int = Field(default=5, ge=1, le=100)


class Document(Model):
    document_id: Text
    source: Text
    services: list[Text]
    title: Text
    sections: list[Text]
    section_ids: list[Text] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


    @model_validator(mode='after')
    def consistent_sections(self) -> 'Document':
        if self.section_ids and (len(self.section_ids) != len(self.sections)
                                 or len(set(self.section_ids)) != len(self.section_ids)):
            raise ValueError('Section IDs must be unique and match the number of sections')
        return self


class Chunk(Model):
    chunk_id: Text
    document_id: Text
    source: Text
    services: list[Text]
    text: Text
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SearchResult(Chunk):
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
