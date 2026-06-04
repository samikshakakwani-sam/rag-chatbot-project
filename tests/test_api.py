"""
End-to-end API tests (Phase 7 success criteria).

Covers: factual retrieval, advisory refusal + educational link, PII rejection,
citation always present & on a verified domain, footer always present,
≤3-sentence responses, and rate limiting.
"""

import re

from backend import config


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


# --- /api/funds ------------------------------------------------------------
def test_funds_lists_five_schemes_and_examples(client):
    r = client.get("/api/funds")
    assert r.status_code == 200
    d = r.json()
    assert len(d["funds"]) == 5
    assert len(d["example_questions"]) == 3
    assert d["last_updated"]                      # footer source present


# --- Factual query ---------------------------------------------------------
def test_factual_query_returns_fund_data(client):
    r = client.post("/api/chat", json={"message": "What is the expense ratio of HDFC Mid-Cap?"})
    assert r.status_code == 200
    d = r.json()
    assert d["refused"] is False
    assert "mid" in d["reply"].lower()
    assert d["tier"] == 1                          # fund + section pinned


def test_citation_always_present_and_verified_domain(client):
    for q in ["What is the exit load of HDFC Small Cap Fund?",
              "minimum SIP for the defence fund",
              "who manages the large cap fund?"]:
        d = client.post("/api/chat", json={"message": q}).json()
        assert d["citation"], f"no citation for: {q}"
        assert config.is_allowed_citation(d["citation"]), f"bad citation domain: {d['citation']}"


def test_footer_always_present(client):
    d = client.post("/api/chat", json={"message": "expense ratio of large cap"}).json()
    assert d["last_updated"]                       # "Last updated from sources" footer


def test_response_never_exceeds_three_sentences(client):
    for q in ["tell me about the gold etf fund",
              "who manages the large cap fund and their experience?",
              "what is the exit load of small cap and its history?"]:
        d = client.post("/api/chat", json={"message": q}).json()
        assert len(_sentences(d["reply"])) <= config.MAX_SENTENCES, q


# --- Advisory refusal ------------------------------------------------------
def test_advisory_query_is_refused_with_educational_link(client):
    for q in ["Which fund is the best to invest in?",
              "Should I buy the mid cap fund?",
              "Will the small cap fund give 20% returns next year?"]:
        d = client.post("/api/chat", json={"message": q}).json()
        assert d["refused"] is True, q
        assert d["refusal_reason"] == "advisory", q
        assert config.is_allowed_citation(d["citation"])      # AMFI/SEBI link
        assert "amfi" in d["citation"].lower()


def test_factual_superlative_is_not_refused(client):
    for q in ["Which fund has the cheapest expense ratio?",
              "What is the minimum SIP across funds?"]:
        d = client.post("/api/chat", json={"message": q}).json()
        assert d["refused"] is False, q


# --- PII rejection ---------------------------------------------------------
def test_pii_input_rejected_and_not_echoed(client):
    pan = "ABCDE1234F"
    d = client.post("/api/chat", json={"message": f"My PAN is {pan}, what is the NAV of gold etf?"}).json()
    assert d["refused"] is True
    assert d["refusal_reason"] == "pii"
    assert pan not in d["reply"]                   # never echoed back
    assert d["citation"] is None


# --- Rate limiting ---------------------------------------------------------
def test_rate_limit_returns_429(client):
    limit = config.RATE_LIMIT_MAX
    responses = [client.post("/api/chat", json={"message": "expense ratio of mid cap"})
                 for _ in range(limit + 2)]
    codes = [r.status_code for r in responses]
    assert codes[:limit] == [200] * limit          # within quota
    assert codes[limit] == 429                       # quota exceeded
    assert "Retry-After" in responses[limit].headers
