"""
Apoyos gubernamentales — admin, jobs and storefront-feed routers.
SKELETON: implemented per the Campo IA contract (see services/apoyos.py).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/apoyos", tags=["apoyos"])
jobs_router = APIRouter(prefix="/jobs/apoyos", tags=["jobs"])
storefront_router = APIRouter(prefix="/storefront", tags=["storefront"])
