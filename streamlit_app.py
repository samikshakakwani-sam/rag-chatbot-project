"""
streamlit_app.py — Streamlit Community Cloud entry point for FundFacts AI.

Reuses the existing backend logic IN-PROCESS (no FastAPI/HTTP layer): the same
compliance flow as POST /api/chat —
    PII guard → advisory guard → hybrid retrieval → Groq → post-process.

Secrets: set GROQ_API_KEY in the Streamlit app's "Secrets" (Settings → Secrets).
If it's missing, the app still runs using the deterministic stub (verbatim facts).
"""

import os

import streamlit as st

# Keyword-only retrieval on Streamlit's free tier: no PyTorch / ChromaDB loaded,
# so it never OOMs. (The Docker/FastAPI deploy keeps full semantic search.)
os.environ.setdefault("DISABLE_SEMANTIC", "1")

# --- Bridge Streamlit secrets → env BEFORE importing backend (config reads env
#     at import time). Streamlit secrets behave like a dict. ----------------------
for _k in ("GROQ_API_KEY", "GROQ_MODEL", "LLM_PROVIDER"):
    try:
        if _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
    except Exception:
        pass
os.environ.setdefault("LLM_PROVIDER", "groq")

import json

from backend import advisory, config, pii, postprocess, refusal
from backend.llm import get_provider
from backend.schemas import ChatResponse

st.set_page_config(page_title="FundFacts AI", page_icon="🏦", layout="centered")


# --- One-time init: build the vector store (gitignored → absent on a fresh
#     deploy) and load the retrieval engine + LLM. Cached for the session. -------
@st.cache_resource(show_spinner="Loading fund corpus & embedding model…")
def _init():
    try:
        from ingestion.embed import build_store, CHROMA_DIR
        if not (CHROMA_DIR / "chroma.sqlite3").exists():
            build_store(verbose=False)
    except Exception as exc:  # OOM/download issues → engine degrades to keyword-only
        print("vector store build skipped:", exc)
    from backend.retrieval import RetrievalEngine
    return RetrievalEngine(), get_provider()


def _last_updated():
    try:
        raw = json.loads(config.CHUNKS_PATH.read_text(encoding="utf-8"))
        return raw.get("_meta", {}).get("built_at")
    except Exception:
        return None


engine, llm = _init()
LAST_UPDATED = _last_updated()

EXAMPLES = [
    "What is the expense ratio of HDFC Mid-Cap?",
    "What is the exit load of HDFC Small Cap Fund?",
    "Who manages the HDFC Defence Fund?",
]


def answer(text: str) -> ChatResponse:
    """Same pipeline as the FastAPI /api/chat endpoint."""
    if pii.contains_pii(text):
        return refusal.pii_refusal(LAST_UPDATED)
    if advisory.is_advisory(text):
        return refusal.advisory_refusal(LAST_UPDATED)
    result = engine.retrieve(text)
    raw = llm.generate(text, result.context_text(), result.citations)
    processed = postprocess.process(raw, result.citations, result.primary_citation)
    return ChatResponse(
        reply=processed.reply, citation=processed.citation,
        sources=result.citations, refused=False, tier=result.tier,
        last_updated=LAST_UPDATED,
    )


def render_msg(role: str, text: str, citation: str | None = None, refused: bool = False):
    with st.chat_message(role, avatar=("🏦" if role == "assistant" else None)):
        st.markdown(("⚠️ " if refused else "") + text)
        if citation:
            st.caption(f"🔗 Source: {citation}")


# --- Header ------------------------------------------------------------------
st.title("🏦 FundFacts AI")
st.caption("Factual answers on 5 HDFC mutual funds — NAV, expense ratio, exit load, "
           "minimum SIP, benchmark, tax, and fund managers.")
st.warning("Facts-only. No investment advice.", icon="🛡️")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Replay history
for m in st.session_state.messages:
    render_msg(m["role"], m["content"], m.get("citation"), m.get("refused", False))

# Quick-question pills (only before the first message)
chosen = None
if not st.session_state.messages:
    st.write("**Common questions**")
    cols = st.columns(3)
    for i, ex in enumerate(EXAMPLES):
        if cols[i].button(ex, use_container_width=True):
            chosen = ex

prompt = st.chat_input("Ask about a fund's NAV, expense ratio, exit load…") or chosen

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_msg("user", prompt)
    with st.chat_message("assistant", avatar="🏦"):
        with st.spinner("Retrieving facts…"):
            resp = answer(prompt)
        st.markdown(("⚠️ " if resp.refused else "") + resp.reply)
        if resp.citation:
            st.caption(f"🔗 Source: {resp.citation}")
    st.session_state.messages.append({
        "role": "assistant", "content": resp.reply,
        "citation": resp.citation, "refused": resp.refused,
    })

if LAST_UPDATED:
    st.divider()
    st.caption(f"Last updated from sources: {LAST_UPDATED}  ·  Facts-only. No investment advice.")
