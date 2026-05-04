from pydantic import BaseModel


class OpportunityAnalyzeResponse(BaseModel):
    opportunity_id: str
    requirements_created: int
    status: str
