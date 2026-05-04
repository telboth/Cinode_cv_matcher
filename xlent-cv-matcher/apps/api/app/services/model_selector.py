from app.core.config import Settings


def resolve_model(settings: Settings, requested_model: str | None) -> str:
    if requested_model:
        if requested_model not in settings.openai_allowed_models:
            raise ValueError("Requested model is not allowed")
        return requested_model

    return settings.openai_model
