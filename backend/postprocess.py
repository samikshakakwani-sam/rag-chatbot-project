"""
backend/postprocess.py
----------------------
Validation & normalisation of the raw LLM reply (Phase 4).

Guarantees on the returned text:
  * at most config.MAX_SENTENCES sentences,
  * a citation that belongs to the verified corpus source set — if the model
    omitted one or cited something not in the allowed set, we attach the
    retrieval's primary source_url instead.

The corpus-freshness footer is carried in the structured `last_updated` field
(rendered by the frontend), so it is not duplicated into the reply text here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from backend import config

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


@dataclass
class ProcessedReply:
    reply: str
    citation: Optional[str]


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _cap_sentences(text: str, n: int) -> str:
    sents = _split_sentences(text)
    if len(sents) <= n:
        return text.strip()
    return " ".join(sents[:n]).strip()


# Sentences that are purely a citation lead-in ("For more information, visit …").
# Once we strip the URL, these add nothing — the citation is surfaced structurally.
_CITATION_FILLER = re.compile(
    r"^\s*(for (more|further)|you can (find|visit|learn|read|see)|to (learn|read|find)"
    r"|learn more|read more|visit|see |check (out|the)|more (info|information|details)"
    r"|source\b|find (it|more|out))",
    re.IGNORECASE,
)


def process(raw: str, allowed_sources: List[str],
            primary_citation: Optional[str]) -> ProcessedReply:
    text = (raw or "").strip()

    # 1) Determine the citation: prefer a URL the model emitted IF it is in the
    #    allowed corpus set; otherwise fall back to the retrieval's primary url.
    cited = None
    for url in _URL_RE.findall(text):
        cleaned = url.rstrip(".,);")
        if cleaned in allowed_sources:
            cited = cleaned
            break
    if cited is None:
        cited = primary_citation

    # Phase 6 hardening: never surface a citation outside the verified domains
    # (groww.in / amfiindia.com / sebi.gov.in), even if it was in allowed_sources.
    if cited and not config.is_allowed_citation(cited):
        cited = primary_citation if config.is_allowed_citation(primary_citation or "") else None

    # 2) Drop trailing sentences that carried the URL or are citation filler,
    #    so the prose doesn't end on a dangling "…visit" after URL removal.
    sentences = _split_sentences(text)
    while sentences:
        last = sentences[-1]
        if _URL_RE.search(last) or _CITATION_FILLER.match(last.strip()):
            sentences.pop()
            continue
        break
    text = " ".join(sentences)

    # 3) Strip any stray inline URLs + tidy whitespace/punctuation.
    text = _URL_RE.sub("", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"\s+([.,])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    # 4) Enforce the sentence cap.
    text = _cap_sentences(text, config.MAX_SENTENCES)

    return ProcessedReply(reply=text, citation=cited)
