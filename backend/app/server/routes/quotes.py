from __future__ import annotations

import random

from fastapi import APIRouter, FastAPI

from backend.app.service.quotes import load_quotes

# Read once at module import.
_QUOTES: list[str] = load_quotes()


def register_quotes_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/api/quotes/random")
    def random_quote() -> dict:
        if not _QUOTES:
            return {"quote": ""}
        return {"quote": random.choice(_QUOTES)}

    app.include_router(router)
