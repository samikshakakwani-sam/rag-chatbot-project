"""
ingestion/chunk.py
------------------
Builds a flat chunk store from data/parsed_sections.json.

Chunk unit : 1 section of 1 fund  →  chunk_id = "{fund_key}::{section_key}"
Total chunks: 5 funds × 9 sections = 45

Each chunk contains:
  - fund_key, fund_name, section_key
  - content   : structured dict (passed as JSON context to the LLM)
  - text       : natural-language serialisation (for compact prompt injection)

Retrieval tiers (two-dimensional lookup)
-----------------------------------------
  Tier 1  fund ✓ + section ✓  →   1 chunk     (~13–158 tokens)
  Tier 2  fund ✓              →   9 chunks     (~660 tokens per fund)
  Tier 3  section ✓           →   5 chunks     (~65–790 tokens)
  Tier 4  no match            →  45 chunks     (~3,325 tokens — full corpus)

Usage (standalone):
    python -m ingestion.chunk
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR     = Path(__file__).resolve().parent.parent
SECTIONS_IN  = BASE_DIR / "data" / "parsed_sections.json"
CHUNKS_OUT   = BASE_DIR / "data" / "chunks.json"

# ---------------------------------------------------------------------------
# Fund keyword → fund_key
# ---------------------------------------------------------------------------
FUND_KEYWORDS: dict[str, list[str]] = {
    "mid-cap":   ["mid-cap", "mid cap", "midcap", "mid-cap opportunities"],
    "large-cap": ["large-cap", "large cap", "largecap"],
    "small-cap": ["small-cap", "small cap", "smallcap"],
    "gold-etf":  ["gold", "gold etf", "gold fund", "gold fof"],
    "defence":   ["defence", "defense"],
}

# Section keyword → section_key  (ordered: most-specific first)
SECTION_KEYWORDS: dict[str, list[str]] = {
    "expense_ratio":        ["expense ratio", "ter", "total expense", "fee", "fees",
                              "charge", "charges", "cost of the fund"],
    "exit_load":            ["exit load", "exit charge", "redemption charge",
                              "withdraw", "sell the fund", "redeem", "penalty"],
    "minimum_investment":   ["minimum sip", "minimum lumpsum", "min sip", "min lumpsum",
                              "minimum investment", "min investment", "how much to invest",
                              "how much to start", "starting amount", "minimum amount"],
    "benchmark":            ["benchmark", "tracks", "tracking index", "index fund",
                              "benchmark index"],
    "tax":                  ["tax", "taxation", "ltcg", "stcg", "capital gains",
                              "stamp duty", "lock-in", "lock in", "elss",
                              "tax saving", "tax benefit"],
    "fund_management":      ["fund manager", "who manages", "managed by",
                              "management team", "portfolio manager"],
    "investment_objective": ["investment objective", "scheme objective", "fund objective",
                              "purpose of the fund", "what does the fund invest",
                              "what does this fund do", "strategy"],
    "fund_house":           ["amc", "fund house", "asset management company",
                              "registrar", "about hdfc", "hdfc amc"],
    # overview holds NAV/AUM/ISIN/rating/launch/category. Listed LAST so every
    # other (more specific) section is matched first; these keywords only catch
    # queries that no earlier section claimed. Without them, "what is the NAV"
    # has no keyword anchor and the semantic fallback can guess the wrong section.
    "overview":             ["nav", "net asset value", "current price", "unit price",
                             "aum", "assets under management", "fund size",
                             "isin", "rating", "rated", "launch", "inception",
                             "category of the fund", "scheme details"],
}

# ---------------------------------------------------------------------------
# Natural-language text serialisers (one per section)
# ---------------------------------------------------------------------------

def _text_overview(fund_name: str, s: dict) -> str:
    return (
        f"{s.get('scheme_name', fund_name)} | "
        f"Category: {s.get('sub_category', 'N/A')} {s.get('category', '')} | "
        f"NAV: {s.get('nav', 'N/A')} as of {s.get('nav_date', 'N/A')} | "
        f"AUM: {s.get('aum', 'N/A')} | "
        f"ISIN: {s.get('isin', 'N/A')} | "
        f"Groww Rating: {s.get('groww_rating', 'N/A')}/5 | "
        f"Plan: {s.get('plan_type', 'N/A')} {s.get('scheme_type', '')} | "
        f"Launch: {s.get('launch_date', 'N/A')} | "
        f"Source: {s.get('source_url', 'N/A')}"
    )


def _text_expense_ratio(fund_name: str, s: dict) -> str:
    note = s.get("note", "N/A")
    note_part = f" {note}" if note not in ("N/A", "") else ""
    return f"{fund_name} expense ratio (TER): {s.get('value', 'N/A')}.{note_part}"


def _text_exit_load(fund_name: str, s: dict) -> str:
    desc = s.get("description", "N/A")
    history = s.get("history", [])
    hist_parts = []
    for h in history:
        hist_parts.append(f"{h.get('description', '')} (effective from {h.get('effective_from', 'N/A')})")
    hist_str = "; ".join(hist_parts) if hist_parts else "No historical data."
    return (
        f"{fund_name} exit load: {desc}. "
        f"Load history: {hist_str}"
    )


def _text_minimum_investment(fund_name: str, s: dict) -> str:
    return (
        f"{fund_name} minimum investment — "
        f"SIP: {s.get('sip', 'N/A')}, "
        f"Lumpsum: {s.get('lumpsum', 'N/A')}, "
        f"Additional purchase: {s.get('additional', 'N/A')}, "
        f"SIP multiplier: {s.get('sip_multiplier', 'N/A')}."
    )


def _text_benchmark(fund_name: str, s: dict) -> str:
    return (
        f"{fund_name} benchmark index: {s.get('full_name', 'N/A')} "
        f"(short: {s.get('short_name', 'N/A')})."
    )


def _text_tax(fund_name: str, s: dict) -> str:
    elss = "Yes — eligible for tax deduction under Section 80C." if s.get("is_elss") else "No."
    lock = s.get("lock_in", "None")
    return (
        f"{fund_name} tax details: "
        f"Stamp duty: {s.get('stamp_duty', 'N/A')}. "
        f"Lock-in period: {lock}. "
        f"ELSS fund: {elss} "
        f"{s.get('ltcg_note', '')} "
        f"{s.get('stcg_note', '')}"
    ).strip()


def _text_fund_management(fund_name: str, s: dict) -> str:
    managers = s.get("managers", [])
    if not managers:
        return f"{fund_name} fund manager: information unavailable."
    parts = [f"{fund_name} is managed by:"]
    for m in managers:
        line = f"  • {m.get('name', 'N/A')} (managing since {m.get('since', 'N/A')})"
        edu = m.get("education", "N/A")
        exp = m.get("experience", "N/A")
        if edu != "N/A":
            line += f" | Education: {edu}"
        if exp != "N/A":
            line += f" | Experience: {exp}"
        parts.append(line)
    return "\n".join(parts)


def _text_investment_objective(fund_name: str, s: dict) -> str:
    return f"{fund_name} investment objective: {s.get('text', 'N/A')}"


def _text_fund_house(fund_name: str, s: dict) -> str:
    return (
        f"Fund house: {s.get('name', 'N/A')} | "
        f"Total AUM: {s.get('total_aum', 'N/A')} | "
        f"Registrar: {s.get('registrar', 'N/A')} | "
        f"AMC page: {s.get('page_url', 'N/A')}"
    )


_TEXT_SERIALISERS = {
    "overview":             _text_overview,
    "expense_ratio":        _text_expense_ratio,
    "exit_load":            _text_exit_load,
    "minimum_investment":   _text_minimum_investment,
    "benchmark":            _text_benchmark,
    "tax":                  _text_tax,
    "fund_management":      _text_fund_management,
    "investment_objective": _text_investment_objective,
    "fund_house":           _text_fund_house,
}

# ---------------------------------------------------------------------------
# Chunk builder
# ---------------------------------------------------------------------------

def build_chunk_store(sections_data: dict) -> dict[str, dict]:
    """
    Build the flat chunk store from parsed_sections data.
    Returns a dict keyed by chunk_id = "{fund_key}::{section_key}".
    """
    store: dict[str, dict] = {}

    for fund_key, fund_data in sections_data.items():
        if fund_key.startswith("_"):
            continue

        sections   = fund_data.get("sections", {})
        source_url = fund_data.get("source_url", "")
        fund_name  = (
            sections.get("overview", {}).get("scheme_name")
            or sections.get("overview", {}).get("fund_name")
            or fund_key
        )

        for section_key, content in sections.items():
            if not content:
                log.warning("[%s::%s] empty section — skipped.", fund_key, section_key)
                continue

            serialiser = _TEXT_SERIALISERS.get(section_key)
            if serialiser is None:
                log.warning("[%s::%s] no text serialiser — skipped.", fund_key, section_key)
                continue

            try:
                text = serialiser(fund_name, content)
            except Exception as exc:
                log.warning("[%s::%s] serialiser error: %s", fund_key, section_key, exc)
                text = json.dumps(content, ensure_ascii=False)

            chunk_id = f"{fund_key}::{section_key}"
            store[chunk_id] = {
                "chunk_id":   chunk_id,
                "fund_key":   fund_key,
                "fund_name":  fund_name,
                "section":    section_key,
                "source_url": source_url,
                "content":    content,
                "text":       text,
            }

    return store


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def identify_fund(query: str) -> Optional[str]:
    """Return the fund_key whose keywords best match the query, or None."""
    q = query.lower()
    for fund_key, keywords in FUND_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return fund_key
    return None


def identify_section(query: str) -> Optional[str]:
    """Return the section_key whose keywords best match the query, or None."""
    q = query.lower()
    for section_key, keywords in SECTION_KEYWORDS.items():
        if keywords and any(kw in q for kw in keywords):
            return section_key
    return None


def get_chunks(
    store: dict[str, dict],
    fund_key: Optional[str] = None,
    section_key: Optional[str] = None,
) -> tuple[list[dict], int]:
    """
    Retrieve chunks by tier.

    Returns (chunks, tier) where tier is 1–4.
      Tier 1  fund ✓ + section ✓  →  1 chunk
      Tier 2  fund ✓              →  up to 9 chunks  (full fund)
      Tier 3  section ✓           →  up to 5 chunks  (section across all funds)
      Tier 4  no match            →  all 45 chunks   (full corpus fallback)
    """
    if fund_key and section_key:
        chunk_id = f"{fund_key}::{section_key}"
        result = [store[chunk_id]] if chunk_id in store else []
        return result, 1

    if fund_key:
        result = [c for c in store.values() if c["fund_key"] == fund_key]
        return result, 2

    if section_key:
        result = [c for c in store.values() if c["section"] == section_key]
        return result, 3

    return list(store.values()), 4


def retrieve(store: dict[str, dict], query: str) -> tuple[list[dict], int]:
    """
    High-level retrieval: identify fund + section from query, return chunks + tier.
    """
    fund    = identify_fund(query)
    section = identify_section(query)
    chunks, tier = get_chunks(store, fund_key=fund, section_key=section)
    log.debug(
        "retrieve: fund=%s  section=%s  tier=%d  chunks=%d",
        fund, section, tier, len(chunks),
    )
    return chunks, tier


# ---------------------------------------------------------------------------
# Persist + load
# ---------------------------------------------------------------------------

def save_chunk_store(store: dict[str, dict]) -> None:
    """Write the chunk store to data/chunks.json."""
    out = {
        "_meta": {
            "built_at":     datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
            "total_chunks": len(store),
            "funds":        sorted({c["fund_key"] for c in store.values()}),
            "sections":     sorted({c["section"]  for c in store.values()}),
        }
    }
    out.update(store)
    CHUNKS_OUT.parent.mkdir(parents=True, exist_ok=True)
    CHUNKS_OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved %d chunks → %s", len(store), CHUNKS_OUT)


def load_chunk_store() -> dict[str, dict]:
    """Load chunks.json; returns only data chunks (skips _meta)."""
    if not CHUNKS_OUT.exists():
        raise FileNotFoundError(
            f"chunks.json not found at {CHUNKS_OUT}. Run ingestion/chunk.py first."
        )
    raw = json.loads(CHUNKS_OUT.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Build from parsed_sections.json
# ---------------------------------------------------------------------------

def build_from_file() -> dict[str, dict]:
    """Load parsed_sections.json, build + save chunk store, return store."""
    if not SECTIONS_IN.exists():
        raise FileNotFoundError(
            f"parsed_sections.json not found at {SECTIONS_IN}. "
            "Run ingestion/parse.py first."
        )
    data   = json.loads(SECTIONS_IN.read_text(encoding="utf-8"))
    store  = build_chunk_store(data)
    save_chunk_store(store)
    log.info(
        "Chunk store built: %d chunks across %d funds × %d sections.",
        len(store),
        len({c["fund_key"] for c in store.values()}),
        len({c["section"]  for c in store.values()}),
    )
    return store


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    build_from_file()
