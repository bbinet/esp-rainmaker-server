from __future__ import annotations

from fastapi import APIRouter

from app.api.internal import vmq_authz as vmq_authz_router
from app.api.v1.routes import auth, claim, health, meta, node, user

api_router = APIRouter()
api_router.include_router(health.router)
# Claim and vmq-authz live at the root (no /v1 prefix) per the SDK and
# VerneMQ contracts.
api_router.include_router(claim.router)
api_router.include_router(vmq_authz_router.router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(meta.router)
v1_router.include_router(auth.router)
v1_router.include_router(user.router)
v1_router.include_router(node.router)
