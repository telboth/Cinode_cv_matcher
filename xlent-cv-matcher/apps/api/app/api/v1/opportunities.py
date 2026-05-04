from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.models.opportunity import Opportunity
from app.models.requirement import Requirement
from app.schemas.analysis import OpportunityAnalyzeResponse
from app.schemas.openai_config import ModelOverrideRequest
from app.schemas.opportunity import OpportunityCreate, OpportunityRead, OpportunityTextExtractResponse
from app.schemas.requirement import RequirementRead
from app.services.document_text_extractor import DocumentExtractionError, extract_uploaded_document_text
from app.services.model_selector import resolve_model
from app.services.openai_client import create_openai_client
from app.services.openai_requirement_extractor import (
    extract_requirement_tools_with_openai,
    extract_requirements_with_openai,
)
from app.services.requirement_extractor import extract_requirements

router = APIRouter(prefix="/opportunities")


@router.post("", response_model=OpportunityRead, status_code=201)
def create_opportunity(payload: OpportunityCreate, db: Session = Depends(get_db)) -> Opportunity:
    opportunity = Opportunity(
        title=payload.title,
        client_name=payload.client_name,
        source_text=payload.source_text,
        language=payload.language,
    )
    db.add(opportunity)
    db.commit()
    db.refresh(opportunity)
    return opportunity


@router.post("/{opportunity_id}/analyze", response_model=OpportunityAnalyzeResponse)
def analyze_opportunity(
    opportunity_id: str,
    payload: ModelOverrideRequest | None = None,
    db: Session = Depends(get_db),
) -> OpportunityAnalyzeResponse:
    opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    db.query(Requirement).filter(Requirement.opportunity_id == opportunity_id).delete()

    settings = get_settings()
    try:
        selected_model = resolve_model(settings, payload.model_override if payload else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = create_openai_client(settings, payload.openai_api_key_override if payload else None)
    requirements = None
    tool_terms: list[str] = []
    if client is not None:
        requirements = extract_requirements_with_openai(
            client=client,
            model=selected_model,
            opportunity_id=opportunity_id,
            text=opportunity.source_text,
            opportunity_title=opportunity.title,
        )
        extracted_tools = extract_requirement_tools_with_openai(
            client=client,
            model=selected_model,
            text=opportunity.source_text,
            opportunity_title=opportunity.title,
        )
        if extracted_tools:
            tool_terms = extracted_tools

    if not requirements:
        requirements = extract_requirements(opportunity_id=opportunity_id, text=opportunity.source_text)

    if requirements and tool_terms:
        existing = {str(req.text or "").strip().lower() for req in requirements}
        for tool in tool_terms[:20]:
            text_value = f"Erfaring med {tool}".strip()
            key = text_value.lower()
            if key in existing:
                continue
            existing.add(key)
            requirements.append(
                Requirement(
                    opportunity_id=opportunity_id,
                    category="should",
                    text=text_value,
                    weight=0.6,
                    extracted_by="ai_tools",
                )
            )
    if requirements:
        db.add_all(requirements)

    opportunity.status = "analyzed"
    db.commit()

    return OpportunityAnalyzeResponse(
        opportunity_id=opportunity_id,
        requirements_created=len(requirements),
        status=opportunity.status,
    )


@router.get("/{opportunity_id}/requirements", response_model=list[RequirementRead])
def list_requirements(opportunity_id: str, db: Session = Depends(get_db)) -> list[Requirement]:
    opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    return (
        db.query(Requirement)
        .filter(Requirement.opportunity_id == opportunity_id)
        .order_by(Requirement.weight.desc())
        .all()
    )


@router.post("/extract-text", response_model=OpportunityTextExtractResponse)
async def extract_opportunity_text(file: UploadFile = File(...)) -> OpportunityTextExtractResponse:
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    content = await file.read()
    try:
        extracted = extract_uploaded_document_text(filename=filename, content=content)
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return OpportunityTextExtractResponse(
        filename=filename,
        detected_type=extracted.detected_type,
        text=extracted.text,
        warnings=extracted.warnings,
    )
