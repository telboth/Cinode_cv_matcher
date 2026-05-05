from functools import lru_cache
from pathlib import Path
import os

from pydantic import BaseModel
from dotenv import load_dotenv


class Settings(BaseModel):
    app_name: str = "XLENT CV Matcher API"
    api_prefix: str = "/api/v1"
    sqlite_path: Path = Path(__file__).resolve().parents[3] / "data" / "app.db"
    openai_api_key: str | None = None
    use_openai_analysis: bool = False
    openai_model: str = "gpt-4.1-mini"
    openai_allowed_models: list[str] = [
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4o-mini",
        "o4-mini",
    ]
    cinode_base_url: str | None = None
    cinode_api_token: str | None = None
    cinode_publish_path: str = "/v0.1/companies/{companyId}/users/{companyUserId}/profile/import"
    enable_cinode_publish: bool = False
    cinode_ui_automation_enabled: bool = False
    cinode_app_url: str = "https://app.cinode.com"
    cinode_ui_headless: bool = True
    cinode_ui_timeout_ms: int = 120000
    cinode_ui_strict_deterministic_default: bool = True
    cors_allow_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    web_dist_dir: str | None = None


@lru_cache
def get_settings() -> Settings:
    env_path = Path(__file__).resolve().parents[4] / ".env"
    load_dotenv(env_path, override=False)

    raw_allowed_models = os.getenv("OPENAI_ALLOWED_MODELS", "").strip()
    env_allowed_models = [item.strip() for item in raw_allowed_models.split(",") if item.strip()] if raw_allowed_models else []
    default_allowed_models = list(Settings.model_fields["openai_allowed_models"].default)
    merged_allowed_models: list[str] = []
    seen_models: set[str] = set()
    for model in [*env_allowed_models, *default_allowed_models]:
        if model in seen_models:
            continue
        seen_models.add(model)
        merged_allowed_models.append(model)

    configured_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    if configured_model not in seen_models:
        merged_allowed_models.insert(0, configured_model)

    raw_cors = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    cors_allow_origins = [item.strip() for item in raw_cors.split(",") if item.strip()] if raw_cors else list(
        Settings.model_fields["cors_allow_origins"].default
    )

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        use_openai_analysis=os.getenv("USE_OPENAI_ANALYSIS", "false").lower() == "true",
        openai_model=configured_model,
        openai_allowed_models=merged_allowed_models,
        cinode_base_url=os.getenv("CINODE_BASE_URL"),
        cinode_api_token=os.getenv("CINODE_API_TOKEN"),
        cinode_publish_path=os.getenv(
            "CINODE_PUBLISH_PATH",
            "/v0.1/companies/{companyId}/users/{companyUserId}/profile/import",
        ),
        enable_cinode_publish=os.getenv("ENABLE_CINODE_PUBLISH", "false").lower() == "true",
        cinode_ui_automation_enabled=os.getenv("CINODE_UI_AUTOMATION_ENABLED", "false").lower() == "true",
        cinode_app_url=os.getenv("CINODE_APP_URL", "https://app.cinode.com"),
        cinode_ui_headless=os.getenv("CINODE_UI_HEADLESS", "true").lower() == "true",
        cinode_ui_timeout_ms=max(15000, int(os.getenv("CINODE_UI_TIMEOUT_MS", "120000"))),
        cinode_ui_strict_deterministic_default=os.getenv(
            "CINODE_UI_STRICT_DETERMINISTIC_DEFAULT", "true"
        ).lower()
        == "true",
        cors_allow_origins=cors_allow_origins,
        web_dist_dir=os.getenv("WEB_DIST_DIR"),
    )
