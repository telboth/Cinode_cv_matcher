import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.employee import Employee
from app.models.profile_snapshot import ProfileSnapshot
from app.schemas.profile_snapshot import SnapshotRead
from app.schemas.source_import import CinodeImportRequest, LatestProfileResponse
from app.services.docx_parser import read_docx_to_payload

router = APIRouter(prefix="/sources")


@router.post("/cinode/import", response_model=SnapshotRead, status_code=201)
def import_from_cinode(payload: CinodeImportRequest, db: Session = Depends(get_db)) -> ProfileSnapshot:
    employee = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    snapshot = ProfileSnapshot(
        employee_id=payload.employee_id,
        source_type="cinode",
        source_ref=payload.source_ref,
        language=payload.language,
        raw_payload_json=payload.payload,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


@router.post("/docx/import", response_model=SnapshotRead, status_code=201)
async def import_from_docx(
    employee_id: str = Form(...),
    language: str = Form("nb"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ProfileSnapshot:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    suffix = Path(file.filename).suffix.lower()
    if suffix != ".docx":
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(await file.read())
        temp_path = tmp.name

    try:
        payload = read_docx_to_payload(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)

    snapshot = ProfileSnapshot(
        employee_id=employee_id,
        source_type="docx",
        source_ref=file.filename,
        language=language,
        raw_payload_json=payload,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


@router.get("/profiles/{employee_id}/latest", response_model=LatestProfileResponse)
def get_latest_profile(employee_id: str, db: Session = Depends(get_db)) -> LatestProfileResponse:
    snapshot = (
        db.query(ProfileSnapshot)
        .filter(ProfileSnapshot.employee_id == employee_id)
        .order_by(ProfileSnapshot.created_at.desc())
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="No profile snapshot found")

    return LatestProfileResponse(
        employee_id=employee_id,
        snapshot_id=snapshot.id,
        source_type=snapshot.source_type,
        payload=snapshot.raw_payload_json,
    )
