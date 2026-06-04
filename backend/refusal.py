"""
backend/refusal.py
------------------
Refusal routing engine. Produces polite, factual refusals (no advice) with a
link to official investor-education resources (AMFI / SEBI). Shared by both the
advisory guardrail and the PII guardrail.
"""

from __future__ import annotations

from backend import config
from backend.schemas import ChatResponse


def advisory_refusal(last_updated: str | None = None) -> ChatResponse:
    reply = (
        "I can share factual information about these funds, but I can't give "
        "investment advice, recommendations, comparisons, or return projections. "
        f"For guidance on choosing investments, see AMFI's investor corner: {config.AMFI_INVESTOR_URL}"
    )
    return ChatResponse(
        reply=reply,
        citation=config.AMFI_INVESTOR_URL,
        sources=[config.AMFI_INVESTOR_URL, config.SEBI_INVESTOR_URL],
        refused=True,
        refusal_reason="advisory",
        last_updated=last_updated,
    )


def pii_refusal(last_updated: str | None = None) -> ChatResponse:
    reply = (
        "For your privacy and security, please don't share personal or sensitive "
        "details (PAN, Aadhaar, account numbers, OTPs, email, or phone). "
        "Ask a general factual question about the funds and I'll help."
    )
    return ChatResponse(
        reply=reply,
        citation=None,
        sources=[],
        refused=True,
        refusal_reason="pii",
        last_updated=last_updated,
    )
