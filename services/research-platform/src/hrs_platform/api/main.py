"""Assemble feature routers without constructing services or connections."""

from fastapi import APIRouter

from hrs_platform.api.routes import books, cards, events, health, library, reviews, runs, uploads

api_router = APIRouter()
api_router.include_router(books.router)
api_router.include_router(cards.router)
api_router.include_router(runs.router)
api_router.include_router(reviews.router)
api_router.include_router(library.router)
api_router.include_router(uploads.router)
api_router.include_router(events.router)
api_router.include_router(health.router)
