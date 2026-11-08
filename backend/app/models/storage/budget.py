"""Monthly budget targets, as persisted in budget.yaml."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BudgetConfig(BaseModel):
    version: int = 1
    # category (top_category form) -> positive monthly target as a string
    targets: dict[str, str] = Field(default_factory=dict)
