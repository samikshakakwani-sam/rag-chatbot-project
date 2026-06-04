# Phase-Wise Implementation Plan — Mutual Fund FAQ Assistant

> Derived from `architecture.md` and `problemStatement.md`

---

## Phase 1 — Data Corpus & Offline Parser ✓

**Goal**: Establish the factual ground truth that all LLM responses will be grounded in.

**Tasks** *(completed)*

- `scripts/parse_funds.py` — `html.parser`-based extraction from 5 HDFC fund pages; source paths externalised to `scripts/fund_sources.json`.
- Fields extracted per fund: NAV, Min SIP, AUM, Expense Ratio, Exit Load, Benchmark, Fund Manager, Investment Objective, Riskometer, Last Updated.
- `data/mutual_funds_data.json` — clean corpus with `_meta.last_updated` timestamp; atomic write (temp-file rename); per-fund validation with `N/A` fallback logging.

**Deliverable**: `data/mutual_funds_data.json` — clean, deterministic factual corpus. ✓

---

## Phase 2 — Ingestion Pipeline, Chunking, Embedding & Scheduler ✓

**Goal**: Live-fetch, parse, chunk, and **embed** all fund pages daily; keep the RAG corpus and its vector store fresh automatically.

### 2.1 Fetch (`ingestion/fetch.py`) *(completed)*
- HTTP GET each corpus URL via `httpx` with 3-attempt exponential back-off (2 s → 5 s → 10 s).
- Browser `User-Agent`; `Accept-Encoding` omitted so httpx handles decompression automatically.
- Saves `data/raw/{key}.html` and `data/raw/{key}_meta.json` (URL, fetched_at, status, byte size).
- On failure: preserves stale HTML; never overwrites with empty content.

### 2.2 Parse (`ingestion/parse.py`) *(completed)*
- **Primary path**: extracts `<script id="__NEXT_DATA__">` JSON blob (Groww Next.js SSR embeds full structured payload ~160 KB per page).
- **Fallback path**: `ChromeStrippingParser` removes nav / header / footer / scripts / ads via tag + CSS-class heuristics; keyword-proximity token extraction.
- `build_sections()` maps raw fields to 9 canonical sections with clean formatting.
- Saves `data/parsed_sections.json`.

### 2.3 Section schema *(completed)*
Nine canonical sections extracted per fund:

| Section | Key fields |
|---|---|
| `overview` | scheme_name, category, NAV, AUM, ISIN, rating, launch_date |
| `expense_ratio` | value (TER %), analysis note |
| `exit_load` | description, historic load entries |
| `minimum_investment` | SIP, lumpsum, additional, multiplier |
| `benchmark` | short_name, full index name |
| `tax` | stamp_duty, lock_in, is_elss, LTCG/STCG notes |
| `fund_management` | manager list (name, since, education, experience) |
| `investment_objective` | scheme objective text |
| `fund_house` | AMC name, total AUM, registrar, URLs |

### 2.4 Chunking Strategy (`ingestion/chunk.py`)

**Approach: Two-dimensional section-level chunking** — not fixed-size token chunking.

The corpus is structured JSON with labeled fields. Fixed-size token windows would split fields arbitrarily (e.g., cutting `fund_management` mid-manager). Section boundaries are the natural semantic unit.

**Chunk unit**: 1 section of 1 fund → `chunk_id = "{fund_key}::{section_key}"` (45 total chunks).

Each chunk carries:
- `fund_key`, `fund_name`, `section_key`, `content` (structured dict), `text` (natural-language string for LLM injection).

**Four retrieval tiers** — all fit within any model's context window:

| Tier | Condition | Tokens injected | Use case |
|---|---|---|---|
| 1 | fund ✓ + section ✓ | 13–158 | "What is the exit load of HDFC Mid-Cap?" |
| 2 | fund ✓ + no section | ~660 | "Tell me about HDFC Small Cap Fund" |
| 3 | no fund + section ✓ | ~65–790 | "What is the minimum SIP across funds?" |
| 4 | no match | ~3,325 | General fallback (full corpus) |

**Section identification** — question-intent keyword classifier:

| Section | Trigger keywords |
|---|---|
| `expense_ratio` | expense ratio, TER, fee, charges, cost |
| `exit_load` | exit load, redemption, withdraw, sell, redeem |
| `minimum_investment` | minimum, min SIP, how much to invest, starting amount |
| `benchmark` | benchmark, index, tracks, tracking |
| `tax` | tax, LTCG, STCG, capital gains, stamp duty, lock-in, ELSS |
| `fund_management` | fund manager, who manages, managed by |
| `investment_objective` | objective, purpose, what does the fund, strategy |
| `fund_house` | AMC, fund house, registrar |
| `overview` | *(fallback)* |

**Fund identification** — keyword matcher:

| Fund key | Trigger terms |
|---|---|
| `mid-cap` | mid-cap, mid cap, midcap |
| `large-cap` | large-cap, large cap, largecap |
| `small-cap` | small-cap, small cap, smallcap |
| `gold-etf` | gold, gold etf, gold fund |
| `defence` | defence, defense |

Output: `data/chunks.json` — flat dict of 45 chunks; consumed directly by the backend RAG engine.

### 2.5 Orchestrator (`ingestion/ingest.py`)
- Runs fetch → parse → chunk → refresh `mutual_funds_data.json` → **embed → ChromaDB** in sequence.
- Per-step error handling: a failed fetch step does not abort parse/chunk/embed of previously-fetched data; the embed step is isolated so a model/load failure never breaks the corpus refresh.
- Writes run summary (steps completed, funds updated, chunks indexed, errors) to logs.

### 2.6 Daily Scheduler (`ingestion/scheduler.py`)
- APScheduler `BlockingScheduler` with a `cron` trigger at **00:00 UTC** daily.
- Calls `ingest.run_full_pipeline()`; logs next scheduled run time on start.

### 2.7 Vector Embedding & Store (`ingestion/embed.py`)

**Approach: local BGE embeddings + persistent ChromaDB** — semantic retrieval on top of the keyword tiers, with **zero paid API calls**.

**Why this stack (decision record):**
- **Embedding model — `BAAI/bge-small-en-v1.5`** (384-dim, ~130 MB, runs locally, free). Chosen over OpenAI `text-embedding-3-small` (paid) and over BGE-base/large because the corpus is only **45 short, structured chunks** — small gives effectively all the retrieval quality at ~1/10th the footprint and a much faster server cold-start. The model name is a single constant (`EMBED_MODEL`) so base/large is a one-line swap if the corpus grows.
- **Vector store — ChromaDB** (`PersistentClient` at `data/chroma/`, cosine space). Chosen over FAISS because every chunk carries `fund_key`/`section` metadata: Chroma's native `where` filtering lets the backend **pre-filter by fund/section then rank by cosine** (hybrid retrieval), which maps directly onto the existing 4-tier classifier. FAISS offers no metadata layer and only wins at a scale (100k+ vectors) far beyond this corpus.

**Mechanics:**
- One vector per chunk; `id = chunk_id` (`"{fund_key}::{section}"`). Writes use `upsert`, so the daily pipeline refreshes vectors in place — **idempotent, no duplicates**.
- Metadata stored per vector: `fund_key`, `fund_name`, `section`, `source_url`.
- BGE requires a retrieval instruction prefix on **queries** (not documents): `"Represent this sentence for searching relevant passages: "`. Exported as `QUERY_INSTRUCTION` so the backend embeds user queries identically.
- Model weights download once on first run and are cached locally → subsequent runs are offline-capable.
- Exposes `build_store()` (used by the orchestrator) and `query()` + a CLI (`python -m ingestion.embed query "..."`) for smoke-testing.

**Deliverable**: `data/chroma/` — persistent BGE-small vector store of all 45 chunks, hybrid-retrieval ready. ✓

---

**Deliverable**: `data/chunks.json` (45 chunks, 4-tier retrieval) + `data/chroma/` (BGE-small vector store) + daily self-updating pipeline. ✓

---

## Phase 3 — Backend Server (FastAPI)

**Goal**: Build the server that orchestrates retrieval, compliance checks, and LLM calls.

### 3.0 Retrieval design — decision record (empirically derived)

Measured on the actual corpus + a 12-query probe (see analysis run):

- **Corpus is tiny — ~2,700 tokens total** (a whole fund = ~660 tokens). Retrieval is therefore **not** about fitting a context window; it's about *precision* (never feed the wrong fund/section), *grounded citations*, and *latency*.
- **Section-classification accuracy, head-to-head:**
  | Method | Accuracy | Characteristic failure |
  |---|---|---|
  | Keyword classifier alone | 8/12 | abstains when no keyword present ("goal", "follow", "cheapest") |
  | Semantic (BGE) top-1 alone | 7/12 | adjacency traps ("lock-in"→exit_load, "tax saving"→objective) |
  | **Hybrid (keyword-first, semantic-fallback)** | **11/12** | only "current NAV"→overview |
  | **+ whole-fund (Tier 2) recovery** | **12/12 grounded** | — |
  - **Fund identification (keyword) = 12/12.** The two methods are *complementary*: keyword is decisive exactly where semantic is fooled by adjacent sections; semantic rescues exactly where keyword abstains.

**Chosen strategy: hybrid, keyword-first with semantic fallback + generous tiering** (NOT pure-vector, NOT keyword-only). The Chroma vector store earns its place as the **fallback + comparative-query path + confidence signal**, not the primary path.

```
retrieve(query):
  1. FUND    = keyword fund match            # 12/12 reliable — the critical filter; never mix funds
  2. SECTION:
       a. kw = keyword section match
       b. if kw:  section = kw               # high precision; wins the adjacency traps
       c. else:   BGE vector search (metadata where=FUND); section = top-1 IF distance < ~0.30 else None
  3. ASSEMBLE by tier (corpus tiny ⇒ bias toward recall):
       FUND + SECTION → that 1 chunk               (Tier 1, ~13–217 tok)
       FUND only      → all 9 chunks of the fund    (Tier 2, ~660 tok)  ← safe net; recovers NAV/overview
       SECTION only   → that section × 5 funds      (Tier 3) for comparative/superlative queries
       neither        → semantic top-k, else full corpus (Tier 4, ~2,700 tok — still cheap)
  4. CITATION = source_url of the matched chunk(s).
```

> The `0.30` cosine-distance gate is an *observed* split (good matches clustered 0.16–0.28, confused 0.30–0.34) on a 12-query probe — a tunable starting threshold, **not** a hard constant.

### 3.1 Module layout & tasks

- Scaffold the FastAPI project under `backend/main.py` (+ `config.py`, `schemas.py`).
- **`backend/retrieval.py`** — `RetrievalEngine` loaded once at startup (chunk store + Chroma collection held in memory); implements the algorithm in §3.0. Reuses `ingestion.chunk.identify_fund/identify_section/get_chunks` and `ingestion.embed` query path.
- Implement `GET /api/funds` — metadata for all 5 schemes + 3 suggested example questions.
- Implement `POST /api/chat` — accepts `{ "message": "..." }`, returns `{ "reply": "...", "citation": "...", ... }`. Flow: **PII filter → advisory detector → retrieve → LLM → post-process**.
- **`backend/pii.py`** — detect/redact PAN, Aadhaar, account numbers, OTPs, emails, phone numbers **before any processing or logging**; raw PII is never logged or echoed. Policy: reject with a privacy notice.
- **`backend/advisory.py`** — classify recommendation / comparison / return-prediction queries ("should I", "best fund", "will it give X%") and route to refusal. Must **not** misfire on factual superlatives ("cheapest by expense ratio", "minimum SIP across funds").
- **`backend/refusal.py`** — polite, factual refusal + link to AMFI/SEBI investor-education resources; one shared engine for both advisory and PII rejections.
- **`backend/llm.py`** — provider interface (`generate(system, context, query)`) so Phase 4 can drop in Claude/Gemini. For Phase 3, a **deterministic stub** composes a grounded reply from the retrieved chunk `text` so the server is fully testable without an API key.

**Deliverable**: A running FastAPI server passing manual smoke tests for factual queries (citation present), advisory refusals (educational link), and PII rejection (input never echoed/logged).

---

## Phase 4 — LLM Orchestration & Prompt Engineering

**Goal**: Ensure every LLM response is strictly factual, properly formatted, and compliant.

**LLM provider — Groq.** OpenAI-compatible chat API via the `groq` Python SDK; fast inference, generous free tier. Model is configurable (`GROQ_MODEL`, default `llama-3.3-70b-versatile`). The provider sits behind the existing `LLMProvider` interface (`generate(query, context, citation)`), so it slots in next to the Phase 3 stub and can be swapped without touching the request flow.

- **Secrets**: `GROQ_API_KEY` lives only in a gitignored `.env` (never hardcoded, never logged). `.env.example` documents the variables. `LLM_PROVIDER=groq` activates it; on any client error the engine **falls back to the deterministic stub** so the server never hard-fails.

**Tasks**

- Implement `System Prompt Builder` (`backend/prompts.py`) — assembles the system prompt embedding the **retrieved chunk context** (not the whole DB — retrieval already narrowed it) and enforcing:
  - Facts-only constraint (no advice, no comparisons, no return calculations); answer only from the supplied context, else say the info isn't available.
  - Response capped at **3 sentences maximum**.
  - Exactly **one citation link** per response, drawn from the official Groww/AMC source URLs passed in context.
- Integrate the **Groq LLM Client** (`backend/llm.py` → `GroqProvider`) — abstracted behind `LLMProvider`; reads model/key from config; deterministic decoding (low temperature) for factual stability.
- Build **Post-Processing & Validation** (`backend/postprocess.py`):
  - Enforce the 3-sentence limit (truncate if exceeded).
  - Verify a citation is present and originates from the verified corpus source set; if missing, attach the retrieval's primary `source_url`.
  - Surface the `Last updated from sources: <date>` footer from the corpus timestamp (carried in the structured `last_updated` field the frontend renders).

**Deliverable**: End-to-end query→response flow producing compliant, citation-backed answers under 3 sentences, generated by Groq with a safe stub fallback.

---

## Phase 5 — Frontend UI

**Goal**: Deliver a clean, Groww-styled chat interface with mandatory compliance elements.

**Tasks**

- Build `frontend/index.html` with inline CSS/JS (no build toolchain needed):
  - **Disclaimer bar**: `"Facts-only. No investment advice."` — persistent and visible at all times.
  - **Welcome message** introducing the assistant and its scope.
  - **3 clickable quick-question pills** (e.g., "What is the expense ratio of HDFC Mid-Cap?").
  - **Chat message area** with slide-in animations for user and assistant bubbles.
  - **Source citation link** rendered beneath each assistant response.
  - **Footer** showing `Last updated from sources: <date>`.
- Implement **light/dark mode toggle** with smooth CSS transitions.
- Add **client-side input validation** — block obviously PII-containing inputs before hitting the server.
- Ensure **responsive layout** across desktop, tablet, and mobile viewports.

**Deliverable**: A fully functional, Groww-styled single-page chat UI connected to the backend.

---

## Phase 6 — Security & Compliance Hardening

**Goal**: Verify all security and regulatory constraints from the problem statement are enforced end-to-end.

**Tasks**

- Audit and test the PII filter with edge-case inputs (masked PANs, formatted phone numbers, etc.).
- Confirm no PAN, Aadhaar, account number, OTP, email, or phone is logged, stored, or echoed back.
- Run adversarial prompts to test advisory guardrails (e.g., "Which is the best fund?", "Will this fund give 20% returns?").
- Confirm all citation links resolve to `groww.in` or official AMC/AMFI/SEBI domains.
- Add rate limiting on `POST /api/chat` to prevent abuse.
- Review CORS configuration — restrict origins to the frontend domain.

**Deliverable**: A signed-off compliance checklist confirming every constraint in the problem statement is met.

---

## Phase 7 — Testing & Final Deliverables

**Goal**: Validate success criteria and produce the required documentation.

**Tasks**

- Write a test suite covering:
  - Factual query → correct fund data returned.
  - Advisory query → refusal response with educational link.
  - PII in input → request rejected or stripped.
  - Citation always present in response.
  - Footer always present in response.
  - Response never exceeds 3 sentences.
  - Daily scheduler triggers ingestion and updates JSON.
- Author the **README** with:
  - Setup and run instructions (local and scheduler).
  - Selected AMC and fund schemes.
  - Architecture overview (RAG approach, ingestion pipeline).
  - Known limitations.
- Final review against all **Success Criteria** from the problem statement:
  - Accurate factual retrieval ✓
  - Strict facts-only responses ✓
  - Valid source citations on every response ✓
  - Proper advisory refusals ✓
  - Clean, minimal, responsive UI ✓

**Deliverable**: Passing test suite, completed README, and a demo-ready application.

---

## Summary Timeline

| Phase | Focus | Key Output |
|-------|-------|------------|
| 1 | Data Corpus & Parser | `mutual_funds_data.json` ✓ |
| 2 | Ingestion + Chunking + Embedding + Scheduler | `chunks.json` (45 chunks, 4-tier retrieval) + `data/chroma/` (BGE-small vector store) ✓ |
| 3 | Backend (FastAPI) | `/api/funds`, `/api/chat`, hybrid RAG + PII + Refusal ✓ |
| 4 | LLM Orchestration | Groq, compliant 3-sentence cited responses ✓ |
| 5 | Frontend UI | Stitch-styled chat + Sources & Compliance page ✓ |
| 6 | Security & Compliance | Rate limiting, CORS lockdown, citation allowlist, PII/advisory audit ✓ |
| 7 | Testing & Docs | 47-test suite + README + demo ✓ |
