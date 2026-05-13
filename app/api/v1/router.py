from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import auth, health, meta, user

api_router = APIRouter()
api_router.include_router(health.router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(meta.router)
v1_router.include_router(auth.router)
v1_router.include_router(user.router)
