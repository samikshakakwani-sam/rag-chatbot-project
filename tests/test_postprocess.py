"""Post-processing & citation-hardening unit tests (Phase 4 + Phase 6)."""

from backend import postprocess
from backend.config import MAX_SENTENCES

GROWW = "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"


def test_caps_to_max_sentences():
    raw = "Fact one. Fact two. Fact three. Fact four. Fact five."
    out = postprocess.process(raw, [GROWW], GROWW)
    n = out.reply.count(".")
    assert n <= MAX_SENTENCES


def test_extracts_allowed_citation_and_strips_inline_url():
    raw = f"The TER is 0.73%. For more information, visit {GROWW}"
    out = postprocess.process(raw, [GROWW], GROWW)
    assert out.citation == GROWW
    assert "http" not in out.reply                # inline URL stripped
    assert not out.reply.rstrip().endswith(("visit", "at"))   # filler sentence dropped


def test_disallowed_domain_is_dropped():
    raw = "The TER is 0.73%. Source: http://evil.example/x"
    out = postprocess.process(raw, ["http://evil.example/x"], GROWW)
    # evil domain must never be surfaced; falls back to verified primary.
    assert out.citation == GROWW


def test_no_citation_when_primary_invalid():
    out = postprocess.process("Some text.", [], "http://evil.example/x")
    assert out.citation is None
