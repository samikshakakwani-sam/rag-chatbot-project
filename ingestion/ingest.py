"""
ingestion/ingest.py
-------------------
Full pipeline orchestrator: fetch → parse → chunk → refresh mutual_funds_data.json.

Each step is isolated: a failed fetch does not abort parse/chunk of data already
on disk. The run summary is logged and returned.

Usage:
    python -m ingestion.ingest          # run once
    python -m ingestion.ingest --dry    # validate config without hitting network
"""

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------

def step_fetch() -> dict:
    """Fetch all 5 fund pages. Returns per-fund fetch metadata."""
    from ingestion.fetch import fetch_all
    log.info("── Step 1: Fetch ──────────────────────────────")
    results = fetch_all()
    ok  = sum(1 for r in results.values() if not r.get("error"))
    log.info("Fetch: %d/%d succeeded.", ok, len(results))
    return results


def step_parse() -> dict:
    """Parse raw HTML → sections. Returns per-fund parse results."""
    from ingestion.parse import parse_all
    log.info("── Step 2: Parse ──────────────────────────────")
    results = parse_all()
    log.info("Parse: %d funds parsed.", len(results))
    return results


def step_chunk() -> dict:
    """Build chunk store from parsed sections. Returns the store."""
    from ingestion.chunk import build_from_file
    log.info("── Step 3: Chunk ──────────────────────────────")
    store = build_from_file()
    log.info("Chunk: %d chunks built.", len(store))
    return store


def step_embed() -> int:
    """Embed chunks into the persistent ChromaDB vector store (BGE-small)."""
    from ingestion.embed import build_store
    log.info("── Step 5: Embed → ChromaDB ───────────────────")
    n = build_store(verbose=False)
    log.info("Embed: %d chunks indexed into vector store.", n)
    return n


def step_refresh_legacy_json(parse_results: dict) -> None:
    """
    Refresh data/mutual_funds_data.json from the latest parsed sections.
    This keeps the legacy flat-record format in sync for any consumers
    that haven't migrated to chunks.json.
    """
    log.info("── Step 4: Refresh mutual_funds_data.json ─────")
    out_path = BASE_DIR / "data" / "mutual_funds_data.json"

    # Load existing to preserve fields not present in parsed sections
    existing: dict = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    updated_at = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    updated: dict = {}

    for fund_key, result in parse_results.items():
        sections = result.get("sections", {})
        ov  = sections.get("overview", {})
        er  = sections.get("expense_ratio", {})
        el  = sections.get("exit_load", {})
        mi  = sections.get("minimum_investment", {})
        bm  = sections.get("benchmark", {})
        fm  = sections.get("fund_management", {})
        io  = sections.get("investment_objective", {})

        managers = fm.get("managers", [])
        manager_names = ", ".join(m.get("name", "") for m in managers if m.get("name"))

        updated[fund_key] = {
            "fund_name":           ov.get("scheme_name",  existing.get(fund_key, {}).get("fund_name",  "N/A")),
            "category":            ov.get("category",     "N/A"),
            "fund_manager":        manager_names or "N/A",
            "launch_date":         ov.get("launch_date",  "N/A"),
            "nav":                 ov.get("nav",          "N/A"),
            "nav_date":            ov.get("nav_date",     "N/A"),
            "fund_size_aum":       ov.get("aum",          "N/A"),
            "expense_ratio":       er.get("value",        "N/A"),
            "rating":              ov.get("groww_rating", "N/A"),
            "risk_rating":         existing.get(fund_key, {}).get("risk_rating", "N/A"),
            "min_sip":             mi.get("sip",          "N/A"),
            "min_lumpsum":         mi.get("lumpsum",      "N/A"),
            "exit_load":           el.get("description",  "N/A"),
            "investment_objective": io.get("text",        "N/A"),
            "benchmark":           bm.get("full_name",    "N/A"),
            "source_url":          result.get("source_url", ""),
        }

    existing.update(updated)
    existing["_meta"] = {
        "last_updated":  updated_at,
        "funds_parsed":  len(updated),
        "funds_failed":  0,
        "failed_keys":   [],
    }

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=out_path.parent, suffix=".tmp", prefix="mf_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, out_path)
        log.info("mutual_funds_data.json refreshed (%d funds).", len(updated))
    except Exception as exc:
        os.unlink(tmp_path)
        log.error("Failed to write mutual_funds_data.json: %s", exc)


# ---------------------------------------------------------------------------
# Dry-run validator
# ---------------------------------------------------------------------------

def dry_run() -> bool:
    """Validate that all config files and source paths exist. Returns True if OK."""
    log.info("Dry-run: validating configuration …")
    ok = True

    sources_path = BASE_DIR / "scripts" / "fund_sources.json"
    if not sources_path.exists():
        log.error("Missing: %s", sources_path)
        ok = False
    else:
        sources = json.loads(sources_path.read_text())
        for key, info in sources.items():
            url = info.get("source_url", "")
            if not url:
                log.warning("[%s] no source_url.", key)

    log.info("Dry-run result: %s", "PASS" if ok else "FAIL")
    return ok


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_full_pipeline() -> dict:
    """
    Run all 4 steps sequentially.  Returns a run-summary dict.
    Errors in individual steps are logged but do not abort later steps
    that can still proceed on already-fetched/parsed data.
    """
    started_at = datetime.now(timezone.utc)
    log.info("══ Ingestion pipeline started at %s ══", started_at.strftime("%d %b %Y %H:%M UTC"))

    summary: dict = {
        "started_at": started_at.isoformat(),
        "steps": {},
    }

    # Step 1 — Fetch
    try:
        fetch_meta = step_fetch()
        fetch_ok   = sum(1 for r in fetch_meta.values() if not r.get("error"))
        summary["steps"]["fetch"] = {"ok": fetch_ok, "total": len(fetch_meta)}
    except Exception as exc:
        log.error("Fetch step failed: %s", exc)
        summary["steps"]["fetch"] = {"error": str(exc)}

    # Step 2 — Parse
    try:
        parse_results = step_parse()
        summary["steps"]["parse"] = {"funds_parsed": len(parse_results)}
    except Exception as exc:
        log.error("Parse step failed: %s", exc)
        summary["steps"]["parse"] = {"error": str(exc)}
        parse_results = {}

    # Step 3 — Chunk
    try:
        store = step_chunk()
        summary["steps"]["chunk"] = {"total_chunks": len(store)}
    except Exception as exc:
        log.error("Chunk step failed: %s", exc)
        summary["steps"]["chunk"] = {"error": str(exc)}

    # Step 4 — Refresh legacy JSON
    if parse_results:
        try:
            step_refresh_legacy_json(parse_results)
            summary["steps"]["refresh_json"] = {"status": "ok"}
        except Exception as exc:
            log.error("Refresh JSON step failed: %s", exc)
            summary["steps"]["refresh_json"] = {"error": str(exc)}

    # Step 5 — Embed into ChromaDB vector store
    try:
        n_embedded = step_embed()
        summary["steps"]["embed"] = {"chunks_indexed": n_embedded}
    except Exception as exc:
        log.error("Embed step failed: %s", exc)
        summary["steps"]["embed"] = {"error": str(exc)}

    finished_at  = datetime.now(timezone.utc)
    elapsed      = (finished_at - started_at).total_seconds()
    summary["finished_at"] = finished_at.isoformat()
    summary["elapsed_s"]   = round(elapsed, 2)

    log.info(
        "══ Pipeline finished in %.1fs ══  steps: %s",
        elapsed,
        {k: ("ok" if "error" not in v else "FAIL") for k, v in summary["steps"].items()},
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if "--dry" in sys.argv:
        dry_run()
    else:
        run_full_pipeline()
