"""FastAPI rendering layer for the framework-free error framework.

Named error_handlers.py (not errors.py) to avoid clashing with the
backend.app.core.errors package. Renders CodedError into the FE-friendly JSON
envelope and turns any unexpected exception into the generic E0000 envelope.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from backend.app.core.errors.errors import (
    HASH_MISMATCH,
    LEDGER_IMBALANCE,
    POSTING_NOT_FOUND,
    TRANSACTION_NOT_FOUND,
    UNEXPECTED_ERROR,
    CodedError,
)
from backend.app.core.errors.ledger import (
    HashMismatchError,
    LedgerValidationError,
    PostingNotFoundError,
    TransactionNotFoundError,
)

_logger = logging.getLogger("backend")

# Routes whose 422 bodies must not echo what was submitted (a password).
_NO_ECHO_PATHS = frozenset({"/api/import/unlock"})
_ECHO_KEYS = ("input", "ctx")


def _envelope(err: CodedError) -> dict:
    data = getattr(err, "data", None)
    return {
        # Only the errors that carry a payload emit `data`; every other
        # envelope keeps exactly the shape the frontend already handles.
        **({"data": data} if data else {}),
        # "detail" mirrors description for backward compatibility with the
        # existing frontend error handling — no FE change needed.
        "detail": err.description,
        "code": err.code,
        "description": err.description,
        "resolution": err.resolution,
        "resolvedByRefresh": err.resolved_by_refresh,
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CodedError)
    async def _handle_coded_error(_request: Request, exc: CodedError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_envelope(exc))

    @app.exception_handler(LedgerValidationError)
    async def _handle_ledger_invalid(
        _request: Request, exc: LedgerValidationError
    ) -> JSONResponse:
        err = LEDGER_IMBALANCE.exc({"lines": "; ".join(exc.messages)})
        return JSONResponse(status_code=err.status, content=_envelope(err))

    @app.exception_handler(TransactionNotFoundError)
    async def _handle_transaction_not_found(
        _request: Request, exc: TransactionNotFoundError
    ) -> JSONResponse:
        err = TRANSACTION_NOT_FOUND.exc({"txn_id": exc.txn_id})
        return JSONResponse(status_code=err.status, content=_envelope(err))

    @app.exception_handler(PostingNotFoundError)
    async def _handle_posting_not_found(
        _request: Request, exc: PostingNotFoundError
    ) -> JSONResponse:
        err = POSTING_NOT_FOUND.exc({"txn_id": exc.txn_id, "account": exc.account})
        return JSONResponse(status_code=err.status, content=_envelope(err))

    @app.exception_handler(HashMismatchError)
    async def _handle_hash_mismatch(
        _request: Request, exc: HashMismatchError
    ) -> JSONResponse:
        err = HASH_MISMATCH.exc({"expected": exc.expected, "actual": exc.actual})
        return JSONResponse(status_code=err.status, content=_envelope(err))

    # Request-validation 422s keep FastAPI's default body everywhere except
    # the unlock route, where `input` (and `ctx`) could carry the password.
    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> Response:
        # The matched route's template, not the URL: a root_path or a mount
        # prefix changes `request.url.path` but not the route.
        route_path = getattr(request.scope.get("route"), "path", None)
        if route_path not in _NO_ECHO_PATHS:
            return await request_validation_exception_handler(request, exc)
        detail = [
            {k: v for k, v in e.items() if k not in _ECHO_KEYS} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(detail)}
        )

    # Broad guard for unexpected errors. This does NOT shadow FastAPI's built-in
    # HTTPException / RequestValidationError handlers — those are registered for
    # more specific exception types and win, so existing HTTPException raisers
    # and request-validation 422s keep their normal {"detail": ...} bodies.
    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        _logger.exception("unexpected error: %s", exc)
        generic = UNEXPECTED_ERROR.exc()
        return JSONResponse(status_code=generic.status, content=_envelope(generic))
