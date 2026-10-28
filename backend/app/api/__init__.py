"""API routers."""

from fastapi import APIRouter

from app.api import analytics, auth, clients, contacts, enrollments, sequences, webhooks

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(clients.router)
api_router.include_router(contacts.router)
api_router.include_router(sequences.router)
api_router.include_router(enrollments.router)
api_router.include_router(analytics.router)
api_router.include_router(webhooks.router)

__all__ = ["api_router"]
