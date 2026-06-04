"""PII detector audit (Phase 6) — edge-case formats."""

import pytest

from backend import pii


@pytest.mark.parametrize("text,kind", [
    ("my pan is ABCDE1234F", "pan"),
    ("PAN: abcde1234f lowercase", "pan"),
    ("aadhaar 1234 5678 9012", "aadhaar"),
    ("aadhaar 1234-5678-9012", "aadhaar"),
    ("aadhaar 123456789012", "aadhaar"),
    ("call me on 9876543210", "phone"),
    ("phone +91 98765 43210", "phone"),
    ("email me at user.name@example.co.in", "email"),
    ("account number 123456789012345", "account"),
    ("the otp is 482913", "otp"),
])
def test_detects_pii(text, kind):
    res = pii.scan(text)
    assert res.found
    assert kind in res.kinds


@pytest.mark.parametrize("text", [
    "What is the expense ratio of HDFC Mid-Cap?",
    "minimum SIP for the gold etf fund",
    "who manages the defence fund",
    "exit load of small cap, redeemed within 1 year",
])
def test_clean_text_has_no_pii(text):
    assert not pii.contains_pii(text)


@pytest.mark.parametrize("text", [
    "reach me at +919876543210",          # 12 digits → ambiguous PAN-less number
    "id 9988776655 0011223344",           # multiple numbers
])
def test_ambiguous_numbers_still_flagged_as_pii(text):
    # Category may be phone/aadhaar/account, but it MUST be caught & rejected.
    assert pii.contains_pii(text)


def test_redaction_masks_value():
    masked = pii.redact("my PAN is ABCDE1234F ok")
    assert "ABCDE1234F" not in masked
    assert "REDACTED_PAN" in masked
