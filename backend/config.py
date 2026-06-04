"""
backend/config.py
-----------------
Central configuration & constants for the Mutual Fund FAQ Assistant backend.
Loads optional .env (LLM keys used in Phase 4); everything has a safe default
so the server boots without any secrets in Phase 3.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # python-dotenv optional at runtime
    pass

# --- Paths -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = DATA_DIR / "chroma"
CHUNKS_PATH = DATA_DIR / "chunks.json"
LEGACY_JSON_PATH = DATA_DIR / "mutual_funds_data.json"

# --- Retrieval -------------------------------------------------------------
COLLECTION_NAME = "fund_chunks"
# Observed cosine-distance split between confident and confused matches
# (good 0.16–0.28, confused 0.30–0.34). Tunable, NOT a hard constant.
SEMANTIC_DISTANCE_GATE = float(os.getenv("SEMANTIC_DISTANCE_GATE", "0.30"))
SEMANTIC_TOP_K = int(os.getenv("SEMANTIC_TOP_K", "3"))

# --- Response policy (Phase 4 enforces; Phase 3 stub honours loosely) ------
MAX_SENTENCES = 3

# --- LLM (Phase 4) ---------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "stub")  # "stub" | "groq"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))  # low → factual stability
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "220"))      # ~3 sentences

# --- Compliance / education resources --------------------------------------
# Official investor-education destinations used in refusal responses.
AMFI_INVESTOR_URL = "https://www.amfiindia.com/investor-corner"
SEBI_INVESTOR_URL = "https://investor.sebi.gov.in/"

DISCLAIMER = "Facts-only. No investment advice."

# Every citation surfaced to the user MUST resolve to one of these domains
# (the verified corpus source + official regulators). Enforced in postprocess.
ALLOWED_CITATION_DOMAINS = [
    d.strip().lower() for d in os.getenv(
        "ALLOWED_CITATION_DOMAINS", "groww.in,amfiindia.com,sebi.gov.in"
    ).split(",") if d.strip()
]

# --- CORS (Phase 6: locked to the frontend origin, no wildcard) ------------
# Same-origin by default since the backend serves the UI. Override via env for
# a separately-hosted frontend.
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://127.0.0.1:8077,http://localhost:8077"
).split(",")

# --- Rate limiting (Phase 6) -----------------------------------------------
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "30"))      # requests per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds


def is_allowed_citation(url: str) -> bool:
    """True if the URL's host is (a subdomain of) an allowed citation domain."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_CITATION_DOMAINS)
