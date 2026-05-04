from pydantic import BaseModel, ConfigDict


class RequirementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str
    category: str
    text: str
    weight: float
    extracted_by: str
