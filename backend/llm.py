"""
backend/llm.py
--------------
LLM provider interface + a deterministic stub.

Phase 3 ships only the stub, which composes a grounded reply directly from the
retrieved chunk text — so the full request flow (PII → advisory → retrieve →
generate → post-process) is testable end-to-end WITHOUT any API key.

Phase 4 will add ClaudeProvider / GeminiProvider implementing the same
`generate()` signature, plus the strict 3-sentence + single-citation prompt.
The factory `get_provider()` selects by config.LLM_PROVIDER.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from backend import config
from backend.prompts import build_system_prompt, build_user_prompt

log = logging.getLogger("backend.llm")


class LLMProvider(Protocol):
    name: str

    def generate(self, query: str, context: str, sources: list[str]) -> str:
        ...


def _first_sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n]).strip()


class DeterministicStub:
    """No-LLM grounded composer used for Phase 3 (and as an offline fallback).

    It does NOT paraphrase or reason — it surfaces the retrieved facts verbatim,
    capped to the response sentence limit. This keeps Phase 3 honest: any answer
    it gives is literally the corpus text, never invented.
    """

    name = "stub"

    def generate(self, query: str, context: str, sources: list[str]) -> str:
        if not context.strip():
            return ("I don't have that information in my fund data. "
                    "Try asking about NAV, expense ratio, exit load, minimum SIP, "
                    "benchmark, tax, fund manager, or the investment objective.")
        # Each chunk's `text` is already a clean single-fact line; take the most
        # relevant lines up to the sentence cap.
        lines = [ln.strip() for ln in context.splitlines() if ln.strip()]
        joined = " ".join(lines)
        return _first_sentences(joined, config.MAX_SENTENCES)


class GroqProvider:
    """Groq chat-completions client (OpenAI-compatible).

    Builds the facts-only system prompt from the retrieved context + allowed
    sources, calls Groq with low temperature for factual stability, and returns
    the raw assistant text. On ANY error it raises, and `get_provider()`'s
    caller (the engine) falls back to the stub — the server never hard-fails.
    """

    name = "groq"

    def __init__(self) -> None:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set")
        from groq import Groq
        self._client = Groq(api_key=config.GROQ_API_KEY)
        self._model = config.GROQ_MODEL
        self._fallback = DeterministicStub()
        log.info("GroqProvider ready (model=%s)", self._model)

    def generate(self, query: str, context: str, sources: list[str]) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": build_system_prompt(context, sources)},
                    {"role": "user", "content": build_user_prompt(query)},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            # Do not leak the key or full request; log type + message only.
            log.warning("Groq call failed (%s) — using deterministic stub.", exc.__class__.__name__)
            return self._fallback.generate(query, context, sources)


def get_provider() -> LLMProvider:
    provider = config.LLM_PROVIDER.lower()
    if provider == "groq":
        try:
            return GroqProvider()
        except Exception as exc:
            log.warning("Groq provider unavailable (%s) — using stub.", exc.__class__.__name__)
            return DeterministicStub()
    return DeterministicStub()
