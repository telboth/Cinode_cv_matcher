from fastapi import APIRouter

from app.api.v1.cinode import router as cinode_router
from app.api.v1.config import router as config_router
from app.api.v1.cv_variants import router as cv_variants_router
from app.api.v1.employees import router as employees_router
from app.api.v1.opportunities import router as opportunities_router
from app.api.v1.sources import router as sources_router

api_router = APIRouter()
api_router.include_router(config_router, tags=["config"])
api_router.include_router(cinode_router, tags=["cinode"])
api_router.include_router(employees_router, tags=["employees"])
api_router.include_router(sources_router, tags=["sources"])
api_router.include_router(opportunities_router, tags=["opportunities"])
api_router.include_router(cv_variants_router, tags=["cv-variants"])
