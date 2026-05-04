from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SnapshotCreate(BaseModel):
    employee_id: str
    source_type: str
    source_ref: str | None = None
    language: str = "nb"
    raw_payload_json: dict[str, Any]


class SnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    source_type: str
    source_ref: str | None
    language: str
    raw_payload_json: dict[str, Any]
    created_at: datetime
