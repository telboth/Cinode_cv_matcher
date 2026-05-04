from pydantic import BaseModel


class CinodeImportRequest(BaseModel):
    employee_id: str
    payload: dict
    source_ref: str | None = None
    language: str = "nb"


class LatestProfileResponse(BaseModel):
    employee_id: str
    snapshot_id: str
    source_type: str
    payload: dict
