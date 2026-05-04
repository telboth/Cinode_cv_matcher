from pydantic import BaseModel, ConfigDict


class CvSuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    variant_id: str
    section_type: str
    original_text: str
    suggested_text: str
    rationale: str
    evidence_json: dict
    status: str


class CvSuggestionUpdate(BaseModel):
    status: str
    suggested_text: str | None = None
