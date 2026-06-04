# FundFacts AI — HDFC Mutual Fund FAQ Assistant

A **facts-only**, citation-backed RAG chatbot that answers questions about five HDFC
mutual fund schemes. It retrieves from a curated corpus of official Groww fund data,
generates concise (≤3 sentence) answers via an LLM, **refuses investment advice**, and
**rejects personal/sensitive data** — by design.

> **Facts-only. No investment advice.**

---

## Success criteria — final review

| Criterion | Status | How |
|---|---|---|
| Accurate factual retrieval | ✅ | Hybrid keyword + BGE-small/ChromaDB retrieval (12/12 on the eval probe) |
| Strict facts-only responses | ✅ | System prompt constrains to retrieved context; ≤3 sentences; advisory queries refused |
| Valid source citation on every response | ✅ | One citation per reply, enforced to `groww.in` / `amfiindia.com` / `sebi.gov.in` |
| Proper advisory refusals | ✅ | Recommendation / comparison / return-prediction → polite refusal + AMFI link |
| PII protection | ✅ | PAN/Aadhaar/phone/email/account/OTP rejected before processing; never logged or echoed |
| Clean, responsive UI | ✅ | Stitch-styled single-page chat + Sources & Compliance page; light/dark |
| Self-updating corpus | ✅ | Daily ingestion pipeline (APScheduler, 00:00 UTC) |
| Test suite | ✅ | 47 passing tests (`pytest`) |

---

## Selected AMC & schemes

**AMC: HDFC Mutual Fund.** Five schemes (Direct–Growth):

| Key | Scheme |
|---|---|
| `mid-cap` | HDFC Mid Cap Fund |
| `large-cap` | HDFC Large Cap Fund |
| `small-cap` | HDFC Small Cap Fund |
| `gold-etf` | HDFC Gold ETF Fund of Fund |
| `defence` | HDFC Defence Fund |

Source: official Groww fund pages (`groww.in/mutual-funds/...`).

---

## Architecture

```
                ┌─────────────── Ingestion (daily, APScheduler 00:00 UTC) ───────────────┐
   Groww  ──►   fetch ──► parse (__NEXT_DATA__) ──► chunk (9 sections × 5 funds = 45)
   pages         │                                        │
                 └──► mutual_funds_data.json               ├──► chunks.json
                                                           └──► embed ──► ChromaDB (BGE-small)
                                                                              │
   Browser  ──►  FastAPI  ──►  PII guard ──► Advisory guard ──► Hybrid Retrieval ──► Groq LLM ──► post-process
   (chat UI)        │              (reject)        (refuse)      (keyword+semantic)   (≤3 sent)    (cap + cite)
                    └──► / , /compliance , /v1 (served same-origin)
```

**Retrieval (hybrid, keyword-first + semantic fallback).** Empirically chosen: keyword
classification alone scored 8/12, semantic alone 7/12, hybrid **12/12** on a query probe.
1. **Fund** identified by keyword (12/12 reliable).
2. **Section** by keyword; if it abstains, a confident BGE-small semantic match (cosine
   distance gate) fills in.
3. **Tiered assembly** (corpus is only ~2,700 tokens, so it biases toward recall):
   fund+section → 1 chunk · fund only → whole fund (9) · section only → across funds (5) ·
   neither → semantic top-k.

**LLM.** Groq (OpenAI-compatible), default `llama-3.3-70b-versatile`, low temperature.
Falls back to a deterministic grounded stub on any error, so the server never hard-fails.

**Vector store.** ChromaDB persistent client; `BAAI/bge-small-en-v1.5` (384-dim, local,
free) — chosen over a paid embedding API and over a heavier vector DB given the tiny corpus.

---

## Project structure

```
ingestion/      fetch.py parse.py chunk.py embed.py ingest.py scheduler.py
backend/        main.py retrieval.py llm.py prompts.py postprocess.py
                pii.py advisory.py refusal.py ratelimit.py config.py schemas.py
frontend/       index.html (Stitch UI)  compliance.html  index_v1.html (original)
data/           chunks.json  mutual_funds_data.json  chroma/  raw/
tests/          test_api.py test_pii.py test_advisory.py test_postprocess.py test_scheduler.py
Docs/           implementation-plan.md  architecture.md  ...
```

---

## Setup & run

```bash
cd "Grow ChatBot"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure secrets (gitignored)
cp .env.example .env          # then set GROQ_API_KEY=...

# Run the server (serves UI + API)
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8077
```

Open:
- **http://127.0.0.1:8077/** — chat UI
- **http://127.0.0.1:8077/compliance** — Sources & Compliance
- **http://127.0.0.1:8077/v1** — original (Phase 5) UI

### Refresh the corpus / run the scheduler

```bash
python -m ingestion.ingest            # one-shot: fetch → parse → chunk → embed
python -m ingestion.scheduler         # blocking daily scheduler (00:00 UTC)
python -m ingestion.scheduler --once  # run the pipeline immediately and exit
```

If you have no `GROQ_API_KEY`, set `LLM_PROVIDER=stub` to run fully offline (answers are
the verbatim retrieved facts, capped to 3 sentences).

---

## Configuration (`.env`)

| Var | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `stub` | `groq` or `stub` |
| `GROQ_API_KEY` | — | Groq key (required for `groq`) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `SEMANTIC_DISTANCE_GATE` | `0.30` | Cosine gate for semantic section fallback |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` | `30` / `60` | `/api/chat` per-IP quota |
| `CORS_ORIGINS` | `http://127.0.0.1:8077,http://localhost:8077` | Allowed origins |
| `ALLOWED_CITATION_DOMAINS` | `groww.in,amfiindia.com,sebi.gov.in` | Citation allowlist |
| `INGEST_HOUR` / `INGEST_MINUTE` / `INGEST_TIMEZONE` | `0` / `0` / `UTC` | Schedule |

---

## API

- `GET /health` — liveness + corpus/semantic status.
- `GET /api/funds` — 5 fund summaries + 3 example questions + `last_updated`.
- `POST /api/chat` — `{ "message": "..." }` → `{ reply, citation, sources, refused, refusal_reason, tier, last_updated, disclaimer }`.

---

## Security & compliance

- **PII filter** (`pii.py`) — PAN, Aadhaar, phone, email, bank account, OTP/CVV/PIN
  detected and **rejected before any processing**; raw input is **never logged** (masked
  form only) or echoed back.
- **Advisory guardrail** (`advisory.py`) — recommendation / comparison / return-prediction
  queries are refused with an AMFI/SEBI investor-education link. Factual superlatives
  ("cheapest TER", "minimum SIP") are correctly allowed.
- **Citations** — exactly one per response, enforced to the verified domain allowlist.
- **Rate limiting** (`ratelimit.py`) — in-memory per-IP sliding window → HTTP 429 + `Retry-After`.
- **CORS** — locked to the frontend origin (no wildcard).

---

## Testing

```bash
python -m pytest          # 47 tests
```

Covers factual retrieval, advisory refusal + educational link, PII rejection (not echoed),
citation present & on a verified domain, footer present, ≤3-sentence cap, rate-limit 429,
and the scheduler trigger path. Tests run with `LLM_PROVIDER=stub` for determinism (no key needed).

---

## Known limitations

- **Corpus scope** — 5 HDFC schemes only; questions about other funds return "I don't have that information."
- **Freshness** — facts are as of the last ingestion run (`last_updated` footer); intraday NAV changes aren't live.
- **Source granularity** — grounded in Groww fund-page data, not raw SID/KIM/factsheet PDFs.
- **Retrieval** — the `0.30` semantic gate is tuned on a small probe; the keyword classifier
  is English-only and rule-based.
- **Rate limiter** — in-memory (single process); a multi-instance deployment needs a shared store (e.g. Redis).
- **Not financial advice** — informational only; not a substitute for a licensed advisor.
