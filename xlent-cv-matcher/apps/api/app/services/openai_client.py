from __future__ import annotations

from typing import Any

from app.core.config import Settings


def _safe_import_openai() -> Any:
    try:
        from openai import OpenAI

        return OpenAI
    except Exception:
        return None


def create_openai_client(settings: Settings, api_key_override: str | None = None) -> Any | None:
    api_key = (api_key_override or "").strip() or settings.openai_api_key
    if not settings.use_openai_analysis or not api_key:
        return None

    OpenAI = _safe_import_openai()
    if OpenAI is None:
        return None

    return OpenAI(api_key=api_key)
