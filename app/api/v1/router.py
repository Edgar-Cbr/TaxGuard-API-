"""API v1 router."""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, keys

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(keys.router, prefix="/keys", tags=["keys"])

# Future endpoint routers:
# api_router.include_router(validators.router, prefix="/validate", tags=["validators"])
# api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
