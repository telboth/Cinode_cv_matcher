from pydantic import BaseModel


class SuggestResponse(BaseModel):
    variant_id: str
    suggestions_created: int
