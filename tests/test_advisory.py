"""Advisory guardrail audit (Phase 6) — adversarial prompts vs. factual ones."""

import pytest

from backend import advisory


@pytest.mark.parametrize("q", [
    "Which fund is the best to invest in?",
    "what is the best fund for me?",
    "Should I buy the mid cap fund?",
    "Is the small cap fund a good investment?",
    "Will this fund give 20% returns?",
    "Which is better, mid cap or large cap?",
    "recommend a fund for retirement",
    "is it worth investing in the defence fund?",
])
def test_advisory_prompts_flagged(q):
    assert advisory.is_advisory(q), q


@pytest.mark.parametrize("q", [
    "Which fund has the cheapest expense ratio?",
    "What is the minimum SIP across funds?",
    "what is the NAV of gold etf?",
    "who manages the large cap fund?",
    "what is the exit load of small cap?",
    "lowest expense ratio fund",
])
def test_factual_prompts_not_flagged(q):
    assert not advisory.is_advisory(q), q
