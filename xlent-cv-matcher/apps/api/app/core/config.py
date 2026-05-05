from functools import lru_cache
from pathlib import Path
import os

from pydantic import BaseModel
from dotenv import dotenv_values


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
    root_dir = Path(__file__).resolve().parents[4]
    env_path = root_dir / ".env"
    secrets_env_override = os.getenv("SECRETS_ENV_PATH", "").strip()
    secrets_env_path = Path(secrets_env_override) if secrets_env_override else (root_dir / "secrets.env")

    file_env = dotenv_values(env_path) if env_path.exists() else {}
    file_secrets = dotenv_values(secrets_env_path) if secrets_env_path.exists() else {}

    def _env_get(name: str, default: str | None = None) -> str | None:
        # Priority:
        # 1) Process environment
        # 2) secrets.env
        # 3) .env
        if name in os.environ:
            value = os.getenv(name)
            if value is not None and str(value).strip() != "":
                return value

        secret_value = file_secrets.get(name)
        if secret_value is not None and str(secret_value).strip() != "":
            return str(secret_value)

        env_value = file_env.get(name)
        if env_value is not None and str(env_value).strip() != "":
            return str(env_value)

        return default

    raw_allowed_models = (_env_get("OPENAI_ALLOWED_MODELS", "") or "").strip()
    env_allowed_models = [item.strip() for item in raw_allowed_models.split(",") if item.strip()] if raw_allowed_models else []
    default_allowed_models = list(Settings.model_fields["openai_allowed_models"].default)
    merged_allowed_models: list[str] = []
    seen_models: set[str] = set()
    for model in [*env_allowed_models, *default_allowed_models]:
        if model in seen_models:
            continue
        seen_models.add(model)
        merged_allowed_models.append(model)

    configured_model = _env_get("OPENAI_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini"
    if configured_model not in seen_models:
        merged_allowed_models.insert(0, configured_model)

    raw_cors = (_env_get("CORS_ALLOW_ORIGINS", "") or "").strip()
    cors_allow_origins = [item.strip() for item in raw_cors.split(",") if item.strip()] if raw_cors else list(
        Settings.model_fields["cors_allow_origins"].default
    )

    return Settings(
        openai_api_key=_env_get("OPENAI_API_KEY"),
        use_openai_analysis=(_env_get("USE_OPENAI_ANALYSIS", "false") or "false").lower() == "true",
        openai_model=configured_model,
        openai_allowed_models=merged_allowed_models,
        cinode_base_url=_env_get("CINODE_BASE_URL"),
        cinode_api_token=_env_get("CINODE_API_TOKEN"),
        cinode_publish_path=_env_get(
            "CINODE_PUBLISH_PATH",
            "/v0.1/companies/{companyId}/users/{companyUserId}/profile/import",
        ),
        enable_cinode_publish=(_env_get("ENABLE_CINODE_PUBLISH", "false") or "false").lower() == "true",
        cinode_ui_automation_enabled=(_env_get("CINODE_UI_AUTOMATION_ENABLED", "false") or "false").lower() == "true",
        cinode_app_url=_env_get("CINODE_APP_URL", "https://app.cinode.com") or "https://app.cinode.com",
        cinode_ui_headless=(_env_get("CINODE_UI_HEADLESS", "true") or "true").lower() == "true",
        cinode_ui_timeout_ms=max(15000, int(_env_get("CINODE_UI_TIMEOUT_MS", "120000") or "120000")),
        cinode_ui_strict_deterministic_default=(_env_get(
            "CINODE_UI_STRICT_DETERMINISTIC_DEFAULT", "true"
        ) or "true").lower()
        == "true",
        cors_allow_origins=cors_allow_origins,
        web_dist_dir=_env_get("WEB_DIST_DIR"),
    )
