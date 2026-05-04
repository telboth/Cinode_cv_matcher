from functools import lru_cache
from pathlib import Path
import base64

from pydantic import BaseModel
from dotenv import dotenv_values, load_dotenv


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


_PLACEHOLDER_MARKERS = (
    "replace",
    "changeme",
    "change-me",
    "example",
    "your_",
    "token_here",
    "api_key_here",
    "<base64",
    "<token",
    "<api",
    "<key",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_env_file_values(path: Path) -> dict[str, str]:
    try:
        parsed = dotenv_values(path)
    except Exception:
        return {}
    return {k: _clean_secret_value(v) for k, v in parsed.items() if isinstance(k, str)}


def _has_any_token_values(values: dict[str, str]) -> bool:
    openai_raw = _clean_secret_value(values.get("OPENAI_API_KEY"))
    cinode_raw = _clean_secret_value(values.get("CINODE_API_TOKEN"))
    return bool(openai_raw or cinode_raw)


def resolve_secrets_file_path() -> Path:
    import os

    configured = os.getenv("CVMATCHER_SECRETS_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()

    user_profile = os.getenv("USERPROFILE", "").strip()
    profile_path = (
        Path(user_profile) / ".xlent-cv-matcher" / "secrets.env"
        if user_profile
        else Path.home() / ".xlent-cv-matcher" / "secrets.env"
    )
    repo_path = _repo_root() / "secrets.env"
    candidates = [profile_path, repo_path]

    existing = [path for path in candidates if path.exists() and path.is_file()]
    if not existing:
        return profile_path

    scored: list[tuple[int, int, Path]] = []
    for idx, path in enumerate(existing):
        values = _load_env_file_values(path)
        openai_raw = _clean_secret_value(values.get("OPENAI_API_KEY"))
        cinode_raw = _clean_secret_value(values.get("CINODE_API_TOKEN"))
        has_openai = _is_actual_openai_key(openai_raw)
        has_cinode = _is_actual_cinode_token(cinode_raw)
        has_any = _has_any_token_values(values)
        score = 0
        if has_openai:
            score += 2
        if has_cinode:
            score += 2
        if has_any:
            score += 1
        scored.append((score, -idx, path))

    scored.sort(reverse=True)
    return scored[0][2]


def _clean_secret_value(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower().strip()
    if not lowered:
        return True
    if lowered in {"none", "null", "undefined"}:
        return True
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return True
    return False


def _is_actual_openai_key(value: str) -> bool:
    cleaned = _clean_secret_value(value)
    if not cleaned or _looks_like_placeholder(cleaned):
        return False
    return cleaned.startswith("sk-") and len(cleaned) >= 20


def _is_actual_cinode_token(value: str) -> bool:
    cleaned = _clean_secret_value(value)
    if not cleaned or _looks_like_placeholder(cleaned):
        return False

    lowered = cleaned.lower()
    if lowered.startswith("basic "):
        blob = cleaned.split(" ", 1)[1].strip() if " " in cleaned else ""
        if not blob:
            return False
        try:
            padding = "=" * (-len(blob) % 4)
            decoded = base64.b64decode(blob + padding).decode("utf-8", errors="ignore")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        access_id, access_secret = decoded.split(":", 1)
        return bool(access_id.strip() and access_secret.strip())

    if lowered.startswith("bearer "):
        token = cleaned.split(" ", 1)[1].strip() if " " in cleaned else ""
        return len(token) >= 16 and not _looks_like_placeholder(token)

    # Support raw token fallback from older setups (without prefix).
    return len(cleaned) >= 16


def get_secrets_file_health() -> dict[str, object]:
    path = resolve_secrets_file_path()
    exists = path.exists() and path.is_file()
    values: dict[str, str] = _load_env_file_values(path) if exists else {}

    openai_raw = values.get("OPENAI_API_KEY", "")
    cinode_raw = values.get("CINODE_API_TOKEN", "")
    has_openai_key = _is_actual_openai_key(openai_raw)
    has_cinode_token = _is_actual_cinode_token(cinode_raw)
    ready = bool(exists and has_openai_key and has_cinode_token)

    warnings: list[str] = []
    if not exists:
        warnings.append("Mangler secrets.env")
    else:
        if not has_openai_key:
            warnings.append("OPENAI_API_KEY mangler eller ser ugyldig ut")
        if not has_cinode_token:
            warnings.append("CINODE_API_TOKEN mangler eller ser ugyldig ut")

    return {
        "path": str(path),
        "exists": exists,
        "has_openai_api_key": has_openai_key,
        "has_cinode_api_token": has_cinode_token,
        "ready": ready,
        "warnings": warnings,
    }


def get_live_secret_value(name: str) -> str | None:
    path = resolve_secrets_file_path()
    if not path.exists() or not path.is_file():
        return None
    values = _load_env_file_values(path)
    value = _clean_secret_value(values.get(name))
    return value or None


@lru_cache
def get_settings() -> Settings:
    import os
    repo_root = _repo_root()
    env_path = repo_root / ".env"
    load_dotenv(env_path, override=False)

    # Optional secrets file outside repo. Values here override .env.
    secrets_path = resolve_secrets_file_path()
    try:
        if secrets_path.exists() and secrets_path.is_file():
            load_dotenv(secrets_path, override=True)
    except Exception:
        # Ignore invalid/missing optional secrets file and continue with .env/environment.
        pass

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
    )
