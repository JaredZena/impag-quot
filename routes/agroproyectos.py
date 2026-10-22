"""
Agroproyectos — storefront and admin routers.
SKELETON: implemented per the Campo IA contract (see services/agroproyectos.py).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/agroproyectos", tags=["agroproyectos"])
storefront_router = APIRouter(prefix="/storefront", tags=["storefront"])
