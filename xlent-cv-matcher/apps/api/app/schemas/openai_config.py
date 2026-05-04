from pydantic import BaseModel


class ModelOverrideRequest(BaseModel):
    model_override: str | None = None
    openai_api_key_override: str | None = None
    suggestion_prompt_override: str | None = None
    force_heuristic: bool = False


class OpenAIModelsResponse(BaseModel):
    default_model: str
    allowed_models: list[str]
    suggestion_mode: str
    suggestion_mode_reason: str
    use_openai_analysis: bool
    has_openai_api_key: bool
    secrets_file_path: str
    secrets_file_exists: bool
    secrets_file_has_openai_api_key: bool
    secrets_file_has_cinode_api_token: bool
    secrets_file_ready: bool
    secrets_file_warnings: list[str]
