from pydantic import BaseModel


class CinodePublishRequest(BaseModel):
    dry_run: bool = True
    credential_id: str | None = None
    title_override: str | None = None


class CinodePublishResponse(BaseModel):
    variant_id: str
    title_used: str
    published: bool
    dry_run: bool
    target_url: str | None = None
    external_id: str | None = None
    detail: str | None = None
