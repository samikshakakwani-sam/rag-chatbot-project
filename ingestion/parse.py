"""
ingestion/parse.py
------------------
Reads raw HTML from data/raw/{key}.html, strips navigation / footer / chrome,
then extracts and tags scheme content into the canonical section schema.

Primary path  : __NEXT_DATA__ JSON (Groww embeds full structured data server-side)
Fallback path : token-based extraction from visible HTML text

Sections produced
-----------------
    overview            — name, category, NAV, AUM, ISIN, rating
    expense_ratio       — TER value + notes
    exit_load           — redemption charges + history
    minimum_investment  — min SIP, min lumpsum, additional purchase
    benchmark           — short name + full index name
    tax                 — stamp duty, lock-in, ELSS flag
    fund_management     — manager profiles (name, since, education, experience)
    investment_objective — stated scheme objective
    fund_house          — AMC name, AUM, registrar, links

Usage (standalone):
    python -m ingestion.parse
"""

import json
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR   = Path(__file__).resolve().parent.parent
RAW_DIR    = BASE_DIR / "data" / "raw"
OUTPUT_DIR = BASE_DIR / "data"

# ---------------------------------------------------------------------------
# Section names (canonical)
# ---------------------------------------------------------------------------
SECTIONS = [
    "overview",
    "expense_ratio",
    "exit_load",
    "minimum_investment",
    "benchmark",
    "tax",
    "fund_management",
    "investment_objective",
    "fund_house",
]

# ---------------------------------------------------------------------------
# HTML chrome stripper  (fallback path)
# ---------------------------------------------------------------------------
_NOISE_PATTERN = re.compile(
    r"(nav|navbar|header|footer|sidebar|breadcrumb|cookie|banner|"
    r"advertisement|social-share|related|newsletter|modal|overlay)",
    re.IGNORECASE,
)

_SKIP_TAGS = frozenset(
    {"script", "style", "nav", "header", "footer",
     "noscript", "iframe", "svg", "button", "form"}
)


class ChromeStrippingParser(HTMLParser):
    """
    Walk the HTML tree and emit only visible text, skipping:
    - script, style, nav, header, footer, noscript, iframe, svg, button, form
    - Elements whose class / id / role match known navigation / ad patterns
    """

    def __init__(self):
        super().__init__()
        self.tokens: list[str] = []
        self._skip_depth: int = 0
        self._stack: list[str] = []

    def _is_noise(self, tag: str, attrs: list) -> bool:
        if tag in _SKIP_TAGS:
            return True
        attr_dict = dict(attrs)
        combined = " ".join(
            filter(None, [attr_dict.get("class", ""),
                          attr_dict.get("id", ""),
                          attr_dict.get("role", "")])
        )
        return bool(_NOISE_PATTERN.search(combined))

    def handle_starttag(self, tag: str, attrs: list):
        self._stack.append(tag)
        if self._skip_depth > 0 or self._is_noise(tag, attrs):
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            t = data.strip()
            if len(t) > 1:
                self.tokens.append(t)


def strip_chrome(html: str) -> list[str]:
    """Return de-duplicated visible text tokens after stripping HTML chrome."""
    p = ChromeStrippingParser()
    p.feed(html)
    deduped: list[str] = []
    prev = None
    for t in p.tokens:
        if t != prev:
            deduped.append(t)
            prev = t
    return deduped


# ---------------------------------------------------------------------------
# __NEXT_DATA__ extractor
# ---------------------------------------------------------------------------
def _extract_next_data(html: str) -> Optional[dict]:
    """Extract and parse the Next.js server-side data JSON blob."""
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        log.warning("__NEXT_DATA__ JSON parse error: %s", exc)
        return None


def _mf_data(next_data: dict) -> Optional[dict]:
    """Drill into props → pageProps → mfServerSideData."""
    try:
        return next_data["props"]["pageProps"]["mfServerSideData"]
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Section builders from __NEXT_DATA__  (primary path)
# ---------------------------------------------------------------------------
def _fmt_currency(value: Any, unit: str = "Cr") -> str:
    """Format a numeric AUM value as a rupee string."""
    try:
        num = float(value)
        return f"₹{num:,.2f} {unit}"
    except (TypeError, ValueError):
        return str(value) if value not in (None, "None") else "N/A"


def _clean(value: Any, suffix: str = "") -> str:
    """Return a clean string; append suffix if value is present."""
    if value in (None, "None", ""):
        return "N/A"
    s = str(value).strip().rstrip(".,")
    return f"{s}{suffix}" if suffix and not s.endswith(suffix) else s


def _fmt_date(raw: str) -> str:
    """Normalise a date string; keep as-is if parsing fails."""
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d %b %Y")
        except (ValueError, TypeError):
            pass
    return str(raw) if raw not in (None, "None") else "N/A"


def build_sections(d: dict, source_url: str = "") -> dict[str, dict]:
    """
    Map mfServerSideData fields → the 9 canonical sections.
    Returns a dict keyed by section name.
    """

    # --- overview ---
    def _fmt_nav(raw: Any) -> str:
        try:
            return f"₹{float(raw):.2f}"
        except (TypeError, ValueError):
            return _clean(raw)

    overview = {
        "scheme_name":    _clean(d.get("scheme_name")),
        "fund_name":      _clean(d.get("fund_name")),
        "category":       _clean(d.get("category")),
        "sub_category":   _clean(d.get("sub_category")),
        "plan_type":      _clean(d.get("plan_type")),
        "scheme_type":    _clean(d.get("scheme_type")),
        "isin":           _clean(d.get("isin")),
        "scheme_code":    _clean(d.get("scheme_code")),
        "nav":            _fmt_nav(d.get("nav")),
        "nav_date":       _fmt_date(d.get("nav_date", "")),
        "aum":            _fmt_currency(d.get("aum")),
        "groww_rating":   _clean(d.get("groww_rating")),
        "launch_date":    _fmt_date(d.get("launch_date", "")),
        "source_url":     source_url,
    }

    # --- expense_ratio ---
    er_val = _clean(d.get("expense_ratio"))
    er_note = "N/A"
    for item in d.get("analysis", []):
        if item.get("analysis_subject") == "expense_analysis":
            er_note = _clean(item.get("analysis_desc"))
            break
    expense_ratio = {
        "value":       f"{er_val}%" if er_val != "N/A" else "N/A",
        "note":        er_note,
    }

    # --- exit_load ---
    history = []
    for entry in d.get("historic_exit_loads", []):
        history.append({
            "description": _clean(entry.get("note")),
            "effective_from": _fmt_date(str(entry.get("as_on_date", ""))[:10]),
        })
    exit_load = {
        "description": _clean(d.get("exit_load")),
        "history":     history,
    }

    # --- minimum_investment ---
    minimum_investment = {
        "lumpsum":        f"₹{_clean(d.get('min_investment_amount'))}",
        "sip":            f"₹{_clean(d.get('min_sip_investment'))}",
        "additional":     f"₹{_clean(d.get('mini_additional_investment'))}",
        "sip_multiplier": f"₹{_clean(d.get('sip_multiplier'))}",
    }

    # --- benchmark ---
    benchmark = {
        "short_name": _clean(d.get("benchmark")),
        "full_name":  _clean(d.get("benchmark_name")),
    }

    # --- tax ---
    lock_in = d.get("lock_in") or {}
    li_years  = lock_in.get("years")
    li_months = lock_in.get("months")
    li_days   = lock_in.get("days")

    if any(v not in (None, 0) for v in [li_years, li_months, li_days]):
        lock_in_str = " ".join(
            filter(None, [
                f"{li_years} year(s)" if li_years else "",
                f"{li_months} month(s)" if li_months else "",
                f"{li_days} day(s)" if li_days else "",
            ])
        )
    else:
        lock_in_str = "None"

    is_elss = "ELSS" in str(d.get("sub_category", "")).upper()

    tax = {
        "stamp_duty":    _clean(d.get("stamp_duty")),
        "lock_in":       lock_in_str,
        "is_elss":       is_elss,
        "ltcg_note":     (
            "Long-term capital gains above ₹1.25 lakh taxed at 12.5% (equity funds)."
            if d.get("category") == "Equity" else
            "Tax treatment depends on holding period; consult a tax advisor."
        ),
        "stcg_note": (
            "Short-term capital gains taxed at 20% (equity funds)."
            if d.get("category") == "Equity" else
            "Short-term: taxed at slab rate."
        ),
    }

    # --- fund_management ---
    managers = []
    for mgr in d.get("fund_manager_details", []):
        managers.append({
            "name":       _clean(mgr.get("person_name")),
            "since":      _fmt_date(str(mgr.get("date_from", ""))[:10]),
            "education":  _clean(mgr.get("education")),
            "experience": _clean(mgr.get("experience")),
        })
    fund_management = {"managers": managers}

    # --- investment_objective ---
    investment_objective = {
        "text": _clean(d.get("description")),
    }

    # --- fund_house ---
    amc = d.get("amc_info") or {}
    fund_house = {
        "name":        _clean(d.get("fund_house") or amc.get("name")),
        "total_aum":   _fmt_currency(amc.get("aum")),
        "description": _clean(amc.get("description")),
        "registrar":   _clean(d.get("registrar_agent", "")).upper() or "N/A",
        "logo_url":    _clean(d.get("logo_url")),
        "page_url":    _clean(d.get("amc_page_url")),
    }

    return {
        "overview":             overview,
        "expense_ratio":        expense_ratio,
        "exit_load":            exit_load,
        "minimum_investment":   minimum_investment,
        "benchmark":            benchmark,
        "tax":                  tax,
        "fund_management":      fund_management,
        "investment_objective": investment_objective,
        "fund_house":           fund_house,
    }


# ---------------------------------------------------------------------------
# Fallback: section extraction from HTML tokens
# ---------------------------------------------------------------------------
_FALLBACK_KEYWORDS: dict[str, list[str]] = {
    "overview":             ["about", "scheme information"],
    "expense_ratio":        ["expense ratio", "total expense ratio", "ter"],
    "exit_load":            ["exit load", "redemption charge"],
    "minimum_investment":   ["minimum sip", "minimum lumpsum", "min investment"],
    "benchmark":            ["fund benchmark", "benchmark index"],
    "tax":                  ["tax", "capital gains", "ltcg", "stcg"],
    "fund_management":      ["fund manager", "managed by"],
    "investment_objective": ["investment objective", "scheme objective"],
    "fund_house":           ["fund house", "amc", "about hdfc"],
}
_CAPTURE_WINDOW = 8


def extract_sections_from_tokens(tokens: list[str]) -> dict[str, dict]:
    """
    Keyword-proximity fallback section extraction from plain text tokens.
    Returns sections with a ``tokens`` list per section.
    """
    tokens_lower = [t.lower() for t in tokens]
    sections: dict[str, dict] = {s: {"tokens": []} for s in SECTIONS}

    for section, keywords in _FALLBACK_KEYWORDS.items():
        for i, tl in enumerate(tokens_lower):
            if any(kw in tl for kw in keywords):
                window = tokens[i: i + _CAPTURE_WINDOW + 1]
                sections[section]["tokens"].extend(window)
                break

    return sections


# ---------------------------------------------------------------------------
# Per-fund parser
# ---------------------------------------------------------------------------
def parse_fund(key: str) -> Optional[dict]:
    """
    Parse one fund from its saved raw HTML.  Returns a structured dict or None.
    """
    html_path = RAW_DIR / f"{key}.html"
    meta_path = RAW_DIR / f"{key}_meta.json"

    if not html_path.exists():
        log.error(
            "[%s] raw HTML not found at %s — run ingestion/fetch.py first.", key, html_path
        )
        return None

    html = html_path.read_text(encoding="utf-8")

    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    source_url  = meta.get("url", "")
    fetched_at  = meta.get("fetched_at", "unknown")

    # --- Primary: __NEXT_DATA__ ---
    next_data   = _extract_next_data(html)
    mf_data_obj = _mf_data(next_data) if next_data else None

    if mf_data_obj:
        log.info("[%s] __NEXT_DATA__ found — building structured sections.", key)
        sections = build_sections(mf_data_obj, source_url=source_url)
        data_source = "next_data"
    else:
        log.warning(
            "[%s] __NEXT_DATA__ unavailable — falling back to HTML token extraction.", key
        )
        tokens  = strip_chrome(html)
        log.info("[%s] %d visible tokens after stripping chrome.", key, len(tokens))
        if len(tokens) < 10:
            log.warning(
                "[%s] very few tokens (%d); page may be client-side rendered.", key, len(tokens)
            )
        sections  = extract_sections_from_tokens(tokens)
        data_source = "html_tokens"

    populated = [s for s, v in sections.items() if v]
    log.info("[%s] sections populated (%s): %s", key, data_source, populated)

    return {
        "key":         key,
        "fetched_at":  fetched_at,
        "source_url":  source_url,
        "data_source": data_source,
        "sections":    sections,
    }


# ---------------------------------------------------------------------------
# Parse all
# ---------------------------------------------------------------------------
def parse_all() -> dict[str, dict]:
    """
    Parse all raw HTML files in data/raw/.
    Writes a summary to data/parsed_sections.json.
    Returns the full result dict keyed by fund key.
    """
    html_files = sorted(RAW_DIR.glob("*.html"))
    if not html_files:
        log.error("No .html files in %s — run ingestion/fetch.py first.", RAW_DIR)
        return {}

    results: dict[str, dict] = {}
    for f in html_files:
        key = f.stem
        r   = parse_fund(key)
        if r:
            results[key] = r

    # Save summary (without full section detail duplicated in each record)
    summary = {
        k: {
            "fetched_at":  v["fetched_at"],
            "source_url":  v["source_url"],
            "data_source": v["data_source"],
            "sections":    v["sections"],
        }
        for k, v in results.items()
    }
    summary["_meta"] = {
        "parsed_at":    datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "funds_parsed": len(results),
    }

    out = OUTPUT_DIR / "parsed_sections.json"
    out.write_text(
        json.dumps(summary, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Saved → %s  (%d funds)", out, len(results))

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parse_all()
