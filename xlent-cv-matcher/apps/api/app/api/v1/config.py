from fastapi import APIRouter

from app.core.config import get_secrets_file_health, get_settings
from app.schemas.openai_config import OpenAIModelsResponse
from app.services.openai_client import create_openai_client

router = APIRouter(prefix="/config")


@router.get("/openai-models", response_model=OpenAIModelsResponse)
def get_openai_models() -> OpenAIModelsResponse:
    settings = get_settings()
    secrets_health = get_secrets_file_health()
    has_api_key = bool(settings.openai_api_key)
    client = create_openai_client(settings)
    llm_enabled = client is not None

    if llm_enabled:
        mode = "llm"
        reason = "OpenAI-klient er konfigurert og aktiv"
    elif not settings.use_openai_analysis:
        mode = "heuristic"
        reason = "LLM er slått av i konfigurasjon (USE_OPENAI_ANALYSIS=false)"
    elif not has_api_key:
        mode = "heuristic"
        reason = "OPENAI_API_KEY mangler"
    else:
        mode = "heuristic"
        reason = "OpenAI SDK/klient er ikke tilgjengelig"

    return OpenAIModelsResponse(
        default_model=settings.openai_model,
        allowed_models=settings.openai_allowed_models,
        suggestion_mode=mode,
        suggestion_mode_reason=reason,
        use_openai_analysis=settings.use_openai_analysis,
        has_openai_api_key=has_api_key,
        secrets_file_path=str(secrets_health["path"]),
        secrets_file_exists=bool(secrets_health["exists"]),
        secrets_file_has_openai_api_key=bool(secrets_health["has_openai_api_key"]),
        secrets_file_has_cinode_api_token=bool(secrets_health["has_cinode_api_token"]),
        secrets_file_ready=bool(secrets_health["ready"]),
        secrets_file_warnings=[str(item) for item in (secrets_health["warnings"] or [])],
    )
