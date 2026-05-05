import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.base import Base, engine
from app.models import cinode_credential, cv_suggestion, cv_variant, employee, opportunity, profile_snapshot, requirement  # noqa: F401

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router, prefix=settings.api_prefix)


def _resolve_web_dist_dir() -> Path | None:
    candidates: list[Path] = []

    if settings.web_dist_dir:
        candidates.append(Path(settings.web_dist_dir))

    env_candidate = os.getenv("WEB_DIST_DIR", "").strip()
    if env_candidate:
        candidates.append(Path(env_candidate))

    project_root = Path(__file__).resolve().parents[3]
    candidates.append(project_root / "apps" / "web" / "dist")
    candidates.append(project_root / "web_dist")

    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


web_dist_dir = _resolve_web_dist_dir()
if web_dist_dir:
    assets_dir = web_dist_dir / "assets"
    if assets_dir.exists() and assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="web-assets")

    index_file = web_dist_dir / "index.html"

    @app.get("/", include_in_schema=False)
    def spa_index() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        normalized = full_path.lstrip("/")
        api_prefix = settings.api_prefix.lstrip("/")
        if normalized.startswith(api_prefix) or normalized in {"docs", "redoc", "openapi.json", "health"}:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not Found")
        file_candidate = web_dist_dir / normalized
        if file_candidate.exists() and file_candidate.is_file():
            return FileResponse(file_candidate)
        return FileResponse(index_file)
