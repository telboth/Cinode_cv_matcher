from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CinodeCredentialCreate(BaseModel):
    label: str
    authorization: str
    base_url: str = "https://api.cinode.com"
    is_default: bool = False


class CinodeCredentialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    base_url: str
    authorization_masked: str
    is_default: bool
    created_at: datetime
    last_test_status: str | None
    last_test_message: str | None
    last_test_at: datetime | None


class CinodeCredentialTestResponse(BaseModel):
    credential_id: str
    ok: bool
    status_code: int | None = None
    message: str
    whoami: dict | None = None


class CinodeConsultantFetchRequest(BaseModel):
    oslo_only: bool = True
    limit: int = 500
    path_override: str | None = None
    cinode_token_override: str | None = None


class CinodeConsultant(BaseModel):
    external_id: str | None = None
    full_name: str
    email: str | None = None
    location: str | None = None
    source: str | None = None


class CinodeConsultantsResponse(BaseModel):
    credential_id: str
    source_path: str | None = None
    company_id: str | None = None
    restricted_to_self: bool = False
    current_user_id: str | None = None
    current_user_name: str | None = None
    access_reason: str | None = None
    total: int
    consultants: list[CinodeConsultant]


class CinodeConsultantCvRequest(BaseModel):
    resume_id: int | None = None
    cinode_token_override: str | None = None


class CinodeConsultantCvResponse(BaseModel):
    credential_id: str
    consultant_id: str
    company_id: str
    source_path: str
    full_name: str
    email: str | None = None
    title: str | None = None
    location: str | None = None
    resumes: list[dict]
    selected_resume_id: int | None = None
    profile: dict
    resume: dict | None = None


class CinodeResumePublicStatusResponse(BaseModel):
    credential_id: str
    consultant_id: str
    resume_id: int | None = None
    checked_url: str | None = None
    public_ready: bool
    status_code: int | None = None
    detail: str | None = None


class CinodeConsultantEnrichResponse(BaseModel):
    credential_id: str
    consultant_id: str
    resume_id: int | None = None
    full_name: str
    linkedin_url: str | None = None
    linkedin_profile_text: str | None = None
    github_url: str | None = None
    orcid_url: str | None = None
    researchgate_url: str | None = None
    scholar_url: str | None = None
    scholar_publications: list[str] = []
    candidate_facts: list[str] = []
    external_findings: list[dict] = []
    sources: list[str] = []
    warnings: list[str] = []


class CinodeBrowserCreateCvRequest(BaseModel):
    variant_id: str
    resume_id: int | None = None
    cinode_token_override: str | None = None
    title_override: str | None = None
    company_slug: Literal["xlent", "differ", "folden"] = "xlent"
    presentation_text_override: str | None = None
    apply_keywords: bool = True
    clean_new_cv_content: bool = False
    enforce_selected_resume_source: bool = True
    strict_deterministic_mode: bool = True


class CinodeBrowserCreateCvResponse(BaseModel):
    ok: bool
    mode: str
    detail: str
    title_used: str
    target_url: str | None = None
    created_resume_url: str | None = None
    screenshot_path: str | None = None
    debug_trace: list[str] = Field(default_factory=list)


class CinodeBrowserPreflightRequest(BaseModel):
    resume_id: int | None = None
    cinode_token_override: str | None = None
    company_slug: Literal["xlent", "differ", "folden"] = "xlent"


class CinodeBrowserPreflightResponse(BaseModel):
    ok: bool
    detail: str
    consultant_id: str
    resume_id: int | None = None
    edit_url: str | None = None
    current_url: str | None = None
    debug_trace: list[str] = Field(default_factory=list)


class CinodeBrowserLoginResponse(BaseModel):
    ok: bool
    detail: str
    current_url: str | None = None
    debug_trace: list[str] = Field(default_factory=list)
