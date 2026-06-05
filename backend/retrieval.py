"""
backend/retrieval.py
--------------------
Hybrid RAG retrieval engine (Phase 3, design recorded in implementation-plan §3.0).

Strategy (empirically derived — keyword 8/12, semantic 7/12, hybrid 12/12 grounded):

    1. FUND    = keyword fund match           (12/12 reliable — the critical filter)
    2. SECTION = keyword section match;
                 else BGE semantic top-1 IF cosine-distance < gate, else None
    3. ASSEMBLE by tier (corpus is ~2,700 tokens, so bias toward recall):
         FUND+SECTION → 1 chunk (Tier 1)
         FUND only    → whole fund, 9 chunks (Tier 2)   ← safe net
         SECTION only → section × 5 funds (Tier 3)      ← comparative queries
         neither      → semantic top-k, else full corpus (Tier 4)
    4. CITATION = source_url of the matched chunk(s).

The engine is constructed ONCE at app startup: the chunk store and the Chroma
collection (with its BGE-small embedding function) are held in memory.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

from ingestion.chunk import (
    identify_fund,
    identify_section,
    get_chunks,
    load_chunk_store,
)
from ingestion.embed import COLLECTION_NAME, QUERY_INSTRUCTION, CHROMA_DIR, _embedding_function

from backend import config

log = logging.getLogger("backend.retrieval")


@dataclass
class RetrievalResult:
    chunks: List[dict]
    tier: int
    fund_key: Optional[str]
    section: Optional[str]
    method: str                       # "keyword" | "semantic" | "keyword+semantic" | "fallback"
    citations: List[str] = field(default_factory=list)

    @property
    def primary_citation(self) -> Optional[str]:
        return self.citations[0] if self.citations else None

    def context_text(self) -> str:
        """Concatenated chunk text for LLM context injection."""
        return "\n\n".join(c["text"] for c in self.chunks)


class RetrievalEngine:
    def __init__(self) -> None:
        self.store = load_chunk_store()
        self._collection = None
        self._semantic_ready = False
        # Lightweight mode for memory-constrained hosts (e.g. Streamlit free tier):
        # skip chromadb + the embedding model entirely → keyword-only retrieval,
        # zero torch/onnx in memory. Set DISABLE_SEMANTIC=1 to enable.
        if os.getenv("DISABLE_SEMANTIC", "").lower() in ("1", "true", "yes"):
            log.info("DISABLE_SEMANTIC set — keyword-only retrieval (no vector store).")
            return
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            self._collection = client.get_collection(
                name=COLLECTION_NAME, embedding_function=_embedding_function()
            )
            self._semantic_ready = self._collection.count() > 0
            log.info("Retrieval engine ready: %d chunks, %d vectors.",
                     len(self.store), self._collection.count())
        except Exception as exc:
            # Degrade gracefully to keyword-only if the vector store is missing.
            log.warning("Vector store unavailable (%s) — keyword-only retrieval.", exc)

    # -- semantic primitive -------------------------------------------------
    def semantic_search(self, query: str, n: int = 3,
                        fund_key: Optional[str] = None,
                        section: Optional[str] = None) -> List[tuple]:
        """Return [(chunk_id, distance), ...] top-n, optional metadata filter."""
        if not self._semantic_ready:
            return []
        where = {}
        if fund_key:
            where["fund_key"] = fund_key
        if section:
            where["section"] = section
        res = self._collection.query(
            query_texts=[QUERY_INSTRUCTION + query],
            n_results=n,
            where=where or None,
        )
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return list(zip(ids, dists))

    # -- main entry ---------------------------------------------------------
    def retrieve(self, query: str) -> RetrievalResult:
        fund = identify_fund(query)
        kw_section = identify_section(query)

        method = "keyword"
        section = kw_section

        # Step 2c: keyword abstained on section → try a confident semantic pick.
        if section is None and self._semantic_ready:
            hits = self.semantic_search(query, n=1, fund_key=fund)
            if hits:
                cid, dist = hits[0]
                if dist < config.SEMANTIC_DISTANCE_GATE:
                    section = cid.split("::", 1)[1]
                    method = "keyword+semantic" if fund else "semantic"

        chunks, tier = get_chunks(self.store, fund_key=fund, section_key=section)

        # Tier 4 (no fund, no section): narrow with semantic top-k instead of
        # dumping all 45; fall back to full corpus only if semantic is down.
        if tier == 4 and self._semantic_ready:
            hits = self.semantic_search(query, n=config.SEMANTIC_TOP_K)
            picked = [self.store[cid] for cid, _ in hits if cid in self.store]
            if picked:
                chunks, method = picked, "semantic"

        citations = _distinct([c.get("source_url", "") for c in chunks if c.get("source_url")])
        result = RetrievalResult(
            chunks=chunks, tier=tier, fund_key=fund, section=section,
            method=method, citations=citations,
        )
        log.debug("retrieve q=%r fund=%s section=%s tier=%d method=%s n=%d",
                  query, fund, section, tier, method, len(chunks))
        return result


def _distinct(items: List[str]) -> List[str]:
    seen, out = set(), []
    for i in items:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out
