"""
ingestion/fetch.py
------------------
HTTP GET each fund corpus URL, save raw HTML + per-fund fetch metadata
to data/raw/.

Usage (standalone):
    python -m ingestion.fetch

Output layout:
    data/raw/{key}.html         — full raw page HTML
    data/raw/{key}_meta.json    — fetch timestamp, status, size
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR       = Path(__file__).resolve().parent.parent
RAW_DIR        = BASE_DIR / "data" / "raw"
SOURCES_CONFIG = BASE_DIR / "scripts" / "fund_sources.json"

# ---------------------------------------------------------------------------
# HTTP config
# ---------------------------------------------------------------------------
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # NOTE: Do NOT set Accept-Encoding manually.
    # httpx decompresses responses automatically only when it controls
    # the Accept-Encoding header. Setting it manually causes the raw
    # compressed bytes to be returned without decompression.
    "Connection":      "keep-alive",
}

_TIMEOUT_S    = 30
_MAX_ATTEMPTS = 3
_BACKOFF_S    = [2, 5, 10]   # delay before each retry attempt


# ---------------------------------------------------------------------------
# Source loader
# ---------------------------------------------------------------------------
def _load_sources() -> dict:
    if not SOURCES_CONFIG.exists():
        raise FileNotFoundError(
            f"fund_sources.json not found at {SOURCES_CONFIG}. "
            "Copy scripts/fund_sources.example.json → scripts/fund_sources.json "
            "and set source_url for each fund."
        )
    with open(SOURCES_CONFIG, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Single fund fetcher
# ---------------------------------------------------------------------------
def fetch_fund(key: str, url: str, client: httpx.Client) -> dict:
    """
    Fetch one fund page.  Saves:
      data/raw/{key}.html       — raw HTML
      data/raw/{key}_meta.json  — fetch metadata

    Returns a metadata dict.  On failure the dict contains an ``error`` key
    and no files are written (or stale files are preserved).
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    html_path = RAW_DIR / f"{key}.html"
    meta_path = RAW_DIR / f"{key}_meta.json"

    last_error: str = "unknown"

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            log.info("[%s] attempt %d — GET %s", key, attempt, url)
            resp = client.get(url, timeout=_TIMEOUT_S)
            resp.raise_for_status()

            html       = resp.text
            fetched_at = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

            # Persist raw HTML
            html_path.write_text(html, encoding="utf-8")

            meta = {
                "key":            key,
                "url":            url,
                "fetched_at":     fetched_at,
                "status_code":    resp.status_code,
                "content_type":   resp.headers.get("content-type", ""),
                "content_length": len(html),
                "html_path":      str(html_path.relative_to(BASE_DIR)),
                "error":          None,
            }
            meta_path.write_text(json.dumps(meta, indent=4), encoding="utf-8")

            log.info(
                "[%s] ✓  %d bytes saved → %s",
                key, len(html), html_path.name,
            )
            return meta

        except httpx.HTTPStatusError as exc:
            last_error = f"HTTP {exc.response.status_code} {exc.response.reason_phrase}"
        except httpx.TimeoutException:
            last_error = f"timeout after {_TIMEOUT_S}s"
        except httpx.RequestError as exc:
            last_error = str(exc)

        log.warning("[%s] attempt %d failed: %s", key, attempt, last_error)

        if attempt < _MAX_ATTEMPTS:
            delay = _BACKOFF_S[attempt - 1]
            log.info("[%s] retrying in %ds …", key, delay)
            time.sleep(delay)

    # All attempts exhausted
    log.error("[%s] ✗  all %d attempts failed. Last error: %s", key, _MAX_ATTEMPTS, last_error)

    error_meta = {
        "key":   key,
        "url":   url,
        "error": last_error,
    }
    # Preserve stale HTML if it exists — do NOT overwrite with nothing
    return error_meta


# ---------------------------------------------------------------------------
# Fetch all funds
# ---------------------------------------------------------------------------
def fetch_all() -> dict[str, dict]:
    """
    Fetch every fund defined in fund_sources.json.
    Returns a dict keyed by fund key with fetch metadata per fund.
    """
    sources = _load_sources()

    results: dict[str, dict] = {}

    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        for key, info in sources.items():
            url = info.get("source_url", "").strip()
            if not url:
                log.warning("[%s] no source_url in fund_sources.json — skipped.", key)
                continue
            results[key] = fetch_fund(key, url, client)

    ok    = sum(1 for r in results.values() if not r.get("error"))
    total = len(results)
    log.info("Fetch complete: %d/%d succeeded.", ok, total)

    if ok < total:
        failed = [k for k, r in results.items() if r.get("error")]
        log.warning("Failed funds: %s", failed)

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    fetch_all()
