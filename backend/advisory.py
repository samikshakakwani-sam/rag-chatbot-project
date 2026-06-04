"""
backend/advisory.py
-------------------
Advisory-query detector. Classifies queries that ask for a *judgment* — a
recommendation, a personalised suitability call, a fund-vs-fund "which is
better", or a return projection — and routes them to the refusal engine.

Design care: it must NOT misfire on factual superlatives that are answerable
from the corpus, e.g. "cheapest by expense ratio", "lowest exit load",
"minimum SIP across funds". Those are data lookups, not advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Recommendation / suitability ("should I…", "is it good", "worth investing")
_RECOMMEND = [
    r"\bshould i\b", r"\bshould we\b", r"\bis it (a )?good\b", r"\bgood (fund|investment|option|choice)\b",
    r"\bworth (it|investing|buying)\b", r"\brecommend", r"\bsuggest .*(fund|invest)",
    # "best" is a recommendation signal in this domain (factual superlatives use
    # cheapest/lowest/highest/minimum, which are whitelisted in _FACTUAL_OVERRIDE).
    r"\bbest (fund|funds|to invest|investment|option|scheme|choice|one|pick|mutual fund)\b",
    r"\bwhich .*\bbest\b", r"\bwhich fund (should|to)\b", r"\bwhich (one|fund) is .*best\b",
    r"\bwhich fund.*\b(invest|buy|choose|pick)\b",
    r"\btop fund\b", r"\bsafe to invest\b", r"\bshould i (buy|invest|put)\b",
    r"\bgood for me\b", r"\bsuitable for me\b", r"\bwhere should i invest\b",
]

# Return prediction / projection ("will it give 20%", "expected returns", "how much will I earn")
_PREDICT = [
    r"\bwill .*(give|return|grow|double|make)\b", r"\bexpected return", r"\bfuture (value|return|nav)\b",
    r"\bhow much (will|would) i (earn|get|make|gain)\b", r"\bpredict", r"\bforecast",
    r"\bguarantee", r"\bmultibagger\b", r"\b\d+\s?%\s?(return|profit|gain|growth)\b",
    r"\bwill i (profit|lose|gain)\b", r"\bprojected\b", r"\btarget price\b",
]

# Comparison / ranking that implies a judgment ("which is better", "X vs Y better")
_COMPARE = [
    r"\bwhich is better\b", r"\bbetter (than|fund|option|choice)\b", r"\bbeat\b",
    r"\bbetter returns\b", r"\boutperform", r"\bvs\.? .*(better|best|good)\b",
]

# Factual superlatives we must allow through (override): answerable from data.
_FACTUAL_OVERRIDE = [
    r"\bcheapest\b", r"\blowest (expense|exit|cost|fee|ter)\b", r"\bhighest (aum|nav)\b",
    r"\bminimum (sip|investment|lumpsum)\b", r"\blowest cost\b", r"\bsmallest minimum\b",
]

_ADVISORY = [re.compile(p, re.IGNORECASE) for p in (_RECOMMEND + _PREDICT + _COMPARE)]
_OVERRIDE = [re.compile(p, re.IGNORECASE) for p in _FACTUAL_OVERRIDE]


@dataclass
class AdvisoryResult:
    is_advisory: bool
    triggers: List[str]


def classify(text: str) -> AdvisoryResult:
    # A clearly-factual superlative wins, even if a soft advisory word co-occurs.
    if any(p.search(text) for p in _OVERRIDE):
        return AdvisoryResult(is_advisory=False, triggers=[])
    triggers = [p.pattern for p in _ADVISORY if p.search(text)]
    return AdvisoryResult(is_advisory=bool(triggers), triggers=triggers)


def is_advisory(text: str) -> bool:
    return classify(text).is_advisory
