from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OpportunityCreate(BaseModel):
    title: str
    client_name: str | None = None
    source_text: str
    language: str = "nb"


class OpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    client_name: str | None
    source_text: str
    language: str
    status: str
    created_at: datetime


class OpportunityTextExtractResponse(BaseModel):
    filename: str
    detected_type: str
    text: str
    warnings: list[str] = Field(default_factory=list)
