"""
backend/main.py
---------------
FastAPI server for the Mutual Fund FAQ Assistant (Phase 3).

Endpoints
  GET  /health      → liveness + resource status
  GET  /api/funds   → metadata for all 5 schemes + 3 suggested questions
  POST /api/chat    → factual Q&A with compliance guards

Chat flow (order matters):
  PII filter → advisory detector → hybrid retrieve → LLM generate → post-process

Resources (retrieval engine, LLM provider, fund summaries) are built once at
startup and held in memory.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend import advisory, config, pii, postprocess, refusal
from backend.llm import get_provider
from backend.ratelimit import limiter
from backend.retrieval import RetrievalEngine
from backend.schemas import ChatRequest, ChatResponse, FundsResponse, FundSummary

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backend.main")

app = FastAPI(title="Mutual Fund FAQ Assistant", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- In-memory resources (populated at startup) ----------------------------
ENGINE: RetrievalEngine | None = None
LLM = get_provider()
FUND_SUMMARIES: list[FundSummary] = []
LAST_UPDATED: str | None = None

EXAMPLE_QUESTIONS = [
    "What is the expense ratio of HDFC Mid-Cap?",
    "What is the exit load of HDFC Small Cap Fund?",
    "Who manages the HDFC Defence Fund?",
]


def _load_last_updated() -> str | None:
    # Prefer the corpus refresh stamp; fall back to the chunk build time.
    try:
        legacy = json.loads(config.LEGACY_JSON_PATH.read_text(encoding="utf-8"))
        if legacy.get("_meta", {}).get("last_updated"):
            return legacy["_meta"]["last_updated"]
    except Exception:
        pass
    try:
        raw = json.loads(config.CHUNKS_PATH.read_text(encoding="utf-8"))
        return raw.get("_meta", {}).get("built_at")
    except Exception:
        return None


def _build_fund_summaries(engine: RetrievalEngine) -> list[FundSummary]:
    summaries: list[FundSummary] = []
    funds = sorted({c["fund_key"] for c in engine.store.values()})
    for fk in funds:
        ov = engine.store.get(f"{fk}::overview", {}).get("content", {})
        er = engine.store.get(f"{fk}::expense_ratio", {}).get("content", {})
        url = engine.store.get(f"{fk}::overview", {}).get("source_url", "")
        name = engine.store.get(f"{fk}::overview", {}).get("fund_name", fk)
        summaries.append(FundSummary(
            fund_key=fk,
            fund_name=name,
            category=f"{ov.get('sub_category', '')} {ov.get('category', '')}".strip() or "N/A",
            nav=str(ov.get("nav", "N/A")),
            expense_ratio=str(er.get("value", "N/A")),
            source_url=url,
        ))
    return summaries


@app.on_event("startup")
def _startup() -> None:
    global ENGINE, FUND_SUMMARIES, LAST_UPDATED
    ENGINE = RetrievalEngine()
    FUND_SUMMARIES = _build_fund_summaries(ENGINE)
    LAST_UPDATED = _load_last_updated()
    log.info("Startup complete: %d funds, last_updated=%s, llm=%s",
             len(FUND_SUMMARIES), LAST_UPDATED, getattr(LLM, "name", "?"))


FRONTEND_DIR = config.BASE_DIR / "frontend"


@app.get("/")
def root() -> FileResponse:
    """Serve the Stitch-styled chat UI same-origin (no CORS needed)."""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/compliance")
def compliance() -> FileResponse:
    """Serve the Sources & Compliance transparency page."""
    return FileResponse(FRONTEND_DIR / "compliance.html")


@app.get("/v1")
def legacy_ui() -> FileResponse:
    """Serve the original Phase 5 chat UI (preserved for comparison)."""
    return FileResponse(FRONTEND_DIR / "index_v1.html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "funds": len(FUND_SUMMARIES),
        "semantic": bool(ENGINE and ENGINE._semantic_ready),
        "last_updated": LAST_UPDATED,
    }


@app.get("/api/funds", response_model=FundsResponse)
def api_funds() -> FundsResponse:
    return FundsResponse(
        funds=FUND_SUMMARIES,
        example_questions=EXAMPLE_QUESTIONS,
        last_updated=LAST_UPDATED,
        disclaimer=config.DISCLAIMER,
    )


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(req: ChatRequest, request: Request):
    # 0) Rate limit per client IP — protects against abuse.
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        retry = limiter.retry_after(client_ip)
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": "Too many requests. Please slow down.", "retry_after": retry},
        )

    message = req.message.strip()

    # 1) PII guard — runs first; raw input is never logged (only masked form).
    pii_hit = pii.scan(message)
    if pii_hit.found:
        log.info("Chat rejected (PII: %s) input=%r", pii_hit.kinds, pii_hit.redacted)
        return refusal.pii_refusal(LAST_UPDATED)

    log.info("Chat query=%r", message)

    # 2) Advisory guard — recommendation / prediction / comparison.
    if advisory.is_advisory(message):
        return refusal.advisory_refusal(LAST_UPDATED)

    # 3) Hybrid retrieval.
    result = ENGINE.retrieve(message)

    # 4) LLM generate (Groq; deterministic stub fallback on any error).
    raw = LLM.generate(message, result.context_text(), result.citations)

    # 5) Post-process: enforce 3-sentence cap + corpus-verified citation.
    processed = postprocess.process(raw, result.citations, result.primary_citation)

    return ChatResponse(
        reply=processed.reply,
        citation=processed.citation,
        sources=result.citations,
        refused=False,
        tier=result.tier,
        last_updated=LAST_UPDATED,
        disclaimer=config.DISCLAIMER,
    )
