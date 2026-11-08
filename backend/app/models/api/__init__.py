"""Pydantic request and response models for the API routes, one module per area.

These describe the *shapes* returned by the report functions in
`backend.app.service.reports`; they are wired via `response_model=` so the OpenAPI
schema (and the generated TypeScript client) carries real response types.

Amounts are decimal strings throughout this codebase (`str()` at the
boundary), so every monetary field is typed `str`, never `float`.

Models describe shapes only and carry no real values / PII.
"""
