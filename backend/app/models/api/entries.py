"""Request models for the manual-entry routes."""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class PostingIn(BaseModel):
    account: str
    amount: str  # decimal string, e.g. "412" or "-412"


class EntryIn(BaseModel):
    date: str  # YYYY-MM-DD
    narration: str
    postings: list[PostingIn]
    tags: list[str] = []

    @field_validator("narration")
    @classmethod
    def narration_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("narration must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def at_least_two_postings(self) -> "EntryIn":
        if len(self.postings) < 2:
            raise ValueError("at least two postings are required")
        return self
