"""
backend/schemas.py
------------------
Pydantic request/response models for the public API.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000,
                         description="The user's factual question about the funds.")


class ChatResponse(BaseModel):
    reply: str
    citation: Optional[str] = None          # primary source_url (one per response)
    sources: List[str] = []                  # all distinct source_urls used
    refused: bool = False                    # True for advisory/PII rejections
    refusal_reason: Optional[str] = None     # "advisory" | "pii" | None
    tier: Optional[int] = None               # retrieval tier (1–4), debug/telemetry
    last_updated: Optional[str] = None       # corpus freshness footer
    disclaimer: str = "Facts-only. No investment advice."


class FundSummary(BaseModel):
    fund_key: str
    fund_name: str
    category: str
    nav: str
    expense_ratio: str
    source_url: str


class FundsResponse(BaseModel):
    funds: List[FundSummary]
    example_questions: List[str]
    last_updated: Optional[str] = None
    disclaimer: str = "Facts-only. No investment advice."
