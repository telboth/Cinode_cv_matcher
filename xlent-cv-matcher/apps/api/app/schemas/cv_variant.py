from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CvVariantCreate(BaseModel):
    employee_id: str
    opportunity_id: str
    base_snapshot_id: str
    title: str


class CvVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    opportunity_id: str
    base_snapshot_id: str
    title: str
    status: str
    created_at: datetime
