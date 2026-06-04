"""
backend/pii.py
--------------
PII detector & redactor. Runs BEFORE any retrieval, LLM call, or logging.

Detects (India-context): PAN, Aadhaar, phone numbers, emails, bank account
numbers, and OTP/CVV-style secrets. Policy = REJECT: the chat endpoint returns
a privacy notice and the raw input is never stored, echoed, or logged.

`redact()` is provided so that any diagnostic logging uses the masked form only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# --- Patterns --------------------------------------------------------------
# PAN: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F)
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
# Aadhaar: 12 digits, optionally grouped 4-4-4 by spaces/hyphens
_AADHAAR = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
# Email
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Indian phone: optional +91/0 prefix (glued or spaced), then 10 digits 6-9.
# Lookbehind/ahead (not \b) so it never matches a slice of a longer digit run
# such as a bank-account number.
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
# Bank account: 9–18 consecutive digits (kept after Aadhaar/phone to avoid overlap)
_ACCOUNT = re.compile(r"\b\d{9,18}\b")
# OTP / CVV / PIN when contextually named (allow a few words between label & digits)
_OTP = re.compile(r"\b(?:otp|cvv|pin|password)\b[^\d]{0,12}\d{3,8}\b", re.IGNORECASE)

_DETECTORS = [
    ("pan", _PAN),
    ("aadhaar", _AADHAAR),
    ("email", _EMAIL),
    ("phone", _PHONE),
    ("otp", _OTP),
    ("account", _ACCOUNT),
]


@dataclass
class PIIResult:
    found: bool
    kinds: List[str]
    redacted: str


def scan(text: str) -> PIIResult:
    """Detect PII and return the kinds found + a redacted copy of the text."""
    kinds: List[str] = []
    redacted = text
    for kind, pattern in _DETECTORS:
        if pattern.search(redacted):
            if kind not in kinds:
                kinds.append(kind)
            redacted = pattern.sub(f"[REDACTED_{kind.upper()}]", redacted)
    return PIIResult(found=bool(kinds), kinds=kinds, redacted=redacted)


def contains_pii(text: str) -> bool:
    return scan(text).found


def redact(text: str) -> str:
    """Masked form safe for logging."""
    return scan(text).redacted
