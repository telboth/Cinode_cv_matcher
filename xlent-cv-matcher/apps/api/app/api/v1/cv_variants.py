from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.models.cinode_credential import CinodeCredential
from app.models.cv_suggestion import CvSuggestion
from app.models.cv_variant import CvVariant
from app.models.employee import Employee
from app.models.opportunity import Opportunity
from app.models.profile_snapshot import ProfileSnapshot
from app.models.requirement import Requirement
from app.schemas.cv_suggestion import CvSuggestionRead, CvSuggestionUpdate
from app.schemas.cv_variant import CvVariantCreate, CvVariantRead
from app.schemas.openai_config import ModelOverrideRequest
from app.schemas.publish import CinodePublishRequest, CinodePublishResponse
from app.schemas.suggestion import SuggestResponse
from app.services.cinode_client import CinodePublishError, publish_to_cinode
from app.services.cinode_payload_mapper import build_cinode_payload
from app.services.model_selector import resolve_model
from app.services.openai_client import create_openai_client
from app.services.suggestion_generator import (
    build_suggestion_prompt_payload,
    heuristic_suggestions,
    openai_suggestions,
    sanitize_suggestion_prompt_payload,
)

router = APIRouter(prefix="/cv-variants")


@router.post("", response_model=CvVariantRead, status_code=201)
def create_cv_variant(payload: CvVariantCreate, db: Session = Depends(get_db)) -> CvVariant:
    employee = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    opportunity = db.query(Opportunity).filter(Opportunity.id == payload.opportunity_id).first()
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    snapshot = db.query(ProfileSnapshot).filter(ProfileSnapshot.id == payload.base_snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Base profile snapshot not found")

    variant = CvVariant(
        employee_id=payload.employee_id,
        opportunity_id=payload.opportunity_id,
        base_snapshot_id=payload.base_snapshot_id,
        title=payload.title,
        status="draft",
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


@router.get("/{variant_id}", response_model=CvVariantRead)
def get_cv_variant(variant_id: str, db: Session = Depends(get_db)) -> CvVariant:
    variant = db.query(CvVariant).filter(CvVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="CV variant not found")
    return variant


@router.post("/{variant_id}/suggest", response_model=SuggestResponse)
def generate_suggestions(
    variant_id: str,
    payload: ModelOverrideRequest | None = None,
    db: Session = Depends(get_db),
) -> SuggestResponse:
    variant = db.query(CvVariant).filter(CvVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="CV variant not found")

    snapshot = db.query(ProfileSnapshot).filter(ProfileSnapshot.id == variant.base_snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Base snapshot not found")

    opportunity = db.query(Opportunity).filter(Opportunity.id == variant.opportunity_id).first()
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    requirements = (
        db.query(Requirement)
        .filter(Requirement.opportunity_id == variant.opportunity_id)
        .order_by(Requirement.weight.desc())
        .all()
    )

    db.query(CvSuggestion).filter(CvSuggestion.variant_id == variant_id).delete()

    settings = get_settings()
    force_heuristic = bool(payload.force_heuristic) if payload else False
    selected_model = None
    if not force_heuristic:
        try:
            selected_model = resolve_model(settings, payload.model_override if payload else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = None if force_heuristic else create_openai_client(
        settings,
        payload.openai_api_key_override if payload else None,
    )
    suggestions = None
    prompt_override = payload.suggestion_prompt_override if payload else None
    if client is not None and selected_model is not None:
        suggestions = openai_suggestions(
            client=client,
            model=selected_model,
            variant_id=variant_id,
            profile_payload=snapshot.raw_payload_json,
            requirements=requirements,
            opportunity_title=opportunity.title,
            opportunity_text=opportunity.source_text,
            suggestion_prompt_override=prompt_override,
        )

    if not suggestions:
        suggestions = heuristic_suggestions(
            variant_id=variant_id,
            profile_payload=snapshot.raw_payload_json,
            requirements=requirements,
            opportunity_title=opportunity.title,
            opportunity_text=opportunity.source_text,
        )

    if suggestions:
        db.add_all(suggestions)

    variant.status = "review"
    db.commit()

    return SuggestResponse(variant_id=variant_id, suggestions_created=len(suggestions))


@router.get("/{variant_id}/suggest/prompt-debug")
def debug_suggestion_prompt_payload(variant_id: str, db: Session = Depends(get_db)) -> dict:
    variant = db.query(CvVariant).filter(CvVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="CV variant not found")

    snapshot = db.query(ProfileSnapshot).filter(ProfileSnapshot.id == variant.base_snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Base snapshot not found")

    opportunity = db.query(Opportunity).filter(Opportunity.id == variant.opportunity_id).first()
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    requirements = (
        db.query(Requirement)
        .filter(Requirement.opportunity_id == variant.opportunity_id)
        .order_by(Requirement.weight.desc())
        .all()
    )

    raw_payload = build_suggestion_prompt_payload(
        profile_payload=snapshot.raw_payload_json,
        requirements=requirements,
        opportunity_title=opportunity.title,
        opportunity_text=opportunity.source_text,
    )
    sanitized_payload = sanitize_suggestion_prompt_payload(raw_payload)

    return {
        "variant_id": variant_id,
        "opportunity_id": opportunity.id,
        "base_snapshot_id": snapshot.id,
        "requirements_count": len(requirements),
        "cv_sections_count": len(sanitized_payload.get("cv_sections", []))
        if isinstance(sanitized_payload, dict)
        else 0,
        "prompt_payload_sanitized": sanitized_payload,
    }


@router.get("/{variant_id}/suggestions", response_model=list[CvSuggestionRead])
def list_suggestions(variant_id: str, db: Session = Depends(get_db)) -> list[CvSuggestion]:
    variant = db.query(CvVariant).filter(CvVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="CV variant not found")

    return db.query(CvSuggestion).filter(CvSuggestion.variant_id == variant_id).all()


@router.patch("/{variant_id}/suggestions/{suggestion_id}", response_model=CvSuggestionRead)
def update_suggestion(
    variant_id: str,
    suggestion_id: str,
    payload: CvSuggestionUpdate,
    db: Session = Depends(get_db),
) -> CvSuggestion:
    if payload.status not in {"pending", "accepted", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    suggestion = (
        db.query(CvSuggestion)
        .filter(CvSuggestion.id == suggestion_id, CvSuggestion.variant_id == variant_id)
        .first()
    )
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    suggestion.status = payload.status
    if payload.suggested_text is not None:
        suggestion.suggested_text = payload.suggested_text

    db.commit()
    db.refresh(suggestion)
    return suggestion


@router.post("/{variant_id}/export/cinode-payload")
def export_cinode_payload(variant_id: str, db: Session = Depends(get_db)) -> dict:
    variant = db.query(CvVariant).filter(CvVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="CV variant not found")

    snapshot = db.query(ProfileSnapshot).filter(ProfileSnapshot.id == variant.base_snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Base snapshot not found")

    suggestions = db.query(CvSuggestion).filter(CvSuggestion.variant_id == variant_id).all()
    payload = build_cinode_payload(snapshot.raw_payload_json, suggestions)

    variant.status = "approved"
    db.commit()

    return {
        "variant_id": variant_id,
        "status": variant.status,
        "cinode_payload": payload,
    }


@router.post("/{variant_id}/publish/cinode", response_model=CinodePublishResponse)
def publish_variant_to_cinode(
    variant_id: str,
    payload: CinodePublishRequest,
    db: Session = Depends(get_db),
) -> CinodePublishResponse:
    variant = db.query(CvVariant).filter(CvVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="CV variant not found")

    snapshot = db.query(ProfileSnapshot).filter(ProfileSnapshot.id == variant.base_snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Base snapshot not found")

    suggestions = db.query(CvSuggestion).filter(CvSuggestion.variant_id == variant_id).all()
    cinode_payload = build_cinode_payload(snapshot.raw_payload_json, suggestions)

    settings = get_settings()
    selected_credential = None
    if payload.credential_id:
        selected_credential = db.query(CinodeCredential).filter(CinodeCredential.id == payload.credential_id).first()
        if not selected_credential:
            raise HTTPException(status_code=404, detail="Cinode credential not found")
    title_used = (payload.title_override or "").strip() or variant.title

    try:
        result = publish_to_cinode(
            settings=settings,
            payload=cinode_payload,
            title=title_used,
            dry_run=payload.dry_run,
            base_url_override=selected_credential.base_url if selected_credential else None,
            auth_value_override=selected_credential.auth_value if selected_credential else None,
        )
    except CinodePublishError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result["published"]:
        variant.status = "exported"
        db.commit()

    return CinodePublishResponse(
        variant_id=variant_id,
        title_used=title_used,
        published=result["published"],
        dry_run=result["dry_run"],
        target_url=result.get("target_url"),
        external_id=result.get("external_id"),
        detail=result.get("detail"),
    )
