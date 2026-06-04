"""
parse_funds.py
--------------
Parses pre-fetched HTML/markdown source files for the 5 HDFC mutual fund pages
and writes a clean, structured JSON corpus to data/mutual_funds_data.json.

Source file paths are configured in scripts/fund_sources.json so they can be
updated without touching this script.

Usage:
    python scripts/parse_funds.py

Output:
    data/mutual_funds_data.json
"""

import os
import re
import json
import tempfile
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent          # project root
SOURCES_CONFIG = Path(__file__).resolve().parent / "fund_sources.json"
OUTPUT_PATH = BASE_DIR / "data" / "mutual_funds_data.json"

# Required fields — any fund missing these will log a warning
REQUIRED_FIELDS = [
    "fund_name", "fund_manager", "nav", "nav_date",
    "expense_ratio", "exit_load", "min_sip", "benchmark",
    "investment_objective", "risk_rating",
]

# ---------------------------------------------------------------------------
# HTML text extractor
# ---------------------------------------------------------------------------
class TextExtractor(HTMLParser):
    """Strips HTML tags and returns a flat list of visible text tokens."""

    def __init__(self):
        super().__init__()
        self.text_list: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            token = data.strip()
            if token:
                self.text_list.append(token)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def clean_value(raw: str) -> str:
    """Strip trailing punctuation (dots, commas) and whitespace from a value."""
    return raw.strip().rstrip(".,")


def find_next(tokens: list[str], label: str) -> str:
    """Return the token immediately after the first occurrence of *label*."""
    label_lower = label.lower()
    for i, t in enumerate(tokens):
        if t.lower() == label_lower and i + 1 < len(tokens):
            return clean_value(tokens[i + 1])
    return "N/A"


def load_sources() -> dict:
    """Load fund source paths from fund_sources.json."""
    if not SOURCES_CONFIG.exists():
        raise FileNotFoundError(
            f"fund_sources.json not found at {SOURCES_CONFIG}. "
            "Create it from fund_sources.example.json."
        )
    with open(SOURCES_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def read_source_file(path: str) -> str:
    """Read a source file; strip Gemini IDE front-matter if present."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # Strip "Title: ...\n---\n" front-matter used by the Gemini IDE cache
    if content.startswith("Title:"):
        parts = content.split("---", 1)
        if len(parts) == 2:
            return parts[1]
    return content


# ---------------------------------------------------------------------------
# Per-fund parser
# ---------------------------------------------------------------------------
def parse_fund(key: str, info: dict) -> dict | None:
    """
    Parse a single fund's source file and return a structured dict.
    Returns None on unrecoverable error (logged).
    """
    source_path = info.get("source_path", "")
    source_url = info.get("source_url", "")

    if not source_path or not os.path.exists(source_path):
        log.error("[%s] Source file not found: %s", key, source_path)
        return None

    try:
        html = read_source_file(source_path)
    except Exception as exc:
        log.error("[%s] Failed to read source file: %s", key, exc)
        return None

    extractor = TextExtractor()
    extractor.feed(html)
    tokens = extractor.text_list

    if not tokens:
        log.error("[%s] No text tokens extracted — HTML may be empty or malformed.", key)
        return None

    # --- About section ---
    about_idx = next(
        (i for i, t in enumerate(tokens) if t.lower() == "about"), -1
    )
    if about_idx == -1 or about_idx + 2 >= len(tokens):
        log.error("[%s] 'About' section not found.", key)
        return None

    fund_name = tokens[about_idx + 1]
    summary = tokens[about_idx + 2]

    # --- Structured table fields ---
    expense_ratio   = find_next(tokens, "expense ratio")
    fund_aum        = find_next(tokens, "fund size (aum)")
    rating          = find_next(tokens, "rating")
    investment_obj  = find_next(tokens, "investment objective")
    benchmark       = find_next(tokens, "fund benchmark")

    # --- Fields parsed from the summary paragraph ---
    def regex_group(pattern: str, text: str, group: int = 1) -> str:
        m = re.search(pattern, text)
        return clean_value(m.group(group)) if m else "N/A"

    fund_manager = regex_group(r"([^.]+) is the Current Fund Manager", summary)
    launch_date  = regex_group(r"made available to investors on ([^.]+)\.", summary)
    risk_rating  = regex_group(r"rated ([^.]+ risk)", summary)
    min_sip      = regex_group(r"Minimum SIP Investment is set to (₹[\d.,\s]+)", summary)
    min_lumpsum  = regex_group(r"Minimum Lumpsum Investment is (₹[\d.,\s]+)", summary)
    exit_load    = regex_group(r"Exit load of ([^.]+)", summary)
    if exit_load != "N/A":
        exit_load = "Exit load of " + exit_load

    nav_match = re.search(r"Latest NAV as of ([^.]+) is (₹[\d.,\s]+)", summary)
    nav        = clean_value(nav_match.group(2)) if nav_match else "N/A"
    nav_date   = clean_value(nav_match.group(1)) if nav_match else "N/A"

    # --- Category inference ---
    text_lower = summary.lower()
    if "equity" in text_lower:
        category = "Equity"
    elif "commodities" in text_lower or "gold" in text_lower:
        category = "Commodities"
    else:
        category = "N/A"

    record = {
        "fund_name":           fund_name,
        "category":            category,
        "fund_manager":        fund_manager,
        "launch_date":         launch_date,
        "nav":                 nav,
        "nav_date":            nav_date,
        "fund_size_aum":       fund_aum,
        "expense_ratio":       expense_ratio,
        "rating":              rating,
        "risk_rating":         risk_rating,
        "min_sip":             min_sip,
        "min_lumpsum":         min_lumpsum,
        "exit_load":           exit_load,
        "investment_objective": investment_obj,
        "benchmark":           benchmark,
        "source_url":          source_url,
    }

    # --- Validate required fields ---
    missing = [f for f in REQUIRED_FIELDS if record.get(f) in (None, "N/A", "")]
    if missing:
        log.warning("[%s] Missing or N/A fields: %s", key, ", ".join(missing))

    return record


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("Loading source configuration from %s", SOURCES_CONFIG)
    try:
        sources = load_sources()
    except FileNotFoundError as exc:
        log.critical(str(exc))
        raise SystemExit(1)

    parsed: dict = {}
    errors: list[str] = []
    run_at = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    for key, info in sources.items():
        log.info("Parsing fund: %s", key)
        record = parse_fund(key, info)
        if record is None:
            errors.append(key)
            log.warning("[%s] Skipped — retained last-good data if available.", key)
        else:
            parsed[key] = record
            log.info("[%s] OK — %s", key, record["fund_name"])

    if not parsed:
        log.critical("All funds failed to parse. Aborting — output file not updated.")
        raise SystemExit(1)

    # --- Merge with existing JSON to preserve last-good data for failed funds ---
    output = {}
    if OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                output = json.load(f)
        except Exception:
            log.warning("Could not read existing output file; starting fresh.")

    output.update(parsed)
    output["_meta"] = {
        "last_updated": run_at,
        "funds_parsed": len(parsed),
        "funds_failed": len(errors),
        "failed_keys":  errors,
    }

    # --- Atomic write (write to temp → rename) ---
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=OUTPUT_PATH.parent, suffix=".tmp", prefix="mutual_funds_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
            json.dump(output, tmp_f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, OUTPUT_PATH)
    except Exception as exc:
        os.unlink(tmp_path)
        log.critical("Failed to write output file: %s", exc)
        raise SystemExit(1)

    log.info(
        "Done. Parsed %d/%d funds. Output: %s",
        len(parsed), len(sources), OUTPUT_PATH,
    )
    if errors:
        log.warning("Failed funds (last-good data retained): %s", errors)


if __name__ == "__main__":
    main()
