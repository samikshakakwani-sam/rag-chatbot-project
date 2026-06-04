# Edge Cases & Corner Scenarios — Mutual Fund FAQ Assistant

> Derived from `architecture.md` and `implementation-plan.md`. Covers every component across all 7 phases.

---

## 1. Data Ingestion & Parser (Phase 1 & 2)

### 1.1 Network & Source Availability

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-01 | One or more Groww URLs return HTTP 4xx / 5xx | Log the failure per URL; retain the last valid JSON for that fund; do not overwrite with partial data |
| EC-02 | All 5 URLs unreachable (network outage) | Abort the run; keep existing `mutual_funds_data.json` intact; emit a critical alert to logs |
| EC-03 | URL returns HTTP 301/302 redirect | Follow redirect up to a configurable max (e.g., 3 hops); fail if redirect chain is broken |
| EC-04 | Groww enforces rate limiting (HTTP 429) | Implement exponential back-off with jitter; retry up to N times before marking URL as failed |
| EC-05 | Request times out (no response within threshold) | Treat as a failed URL; fall back to cached data; log timeout duration |

### 1.2 HTML Structure & Parsing

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-06 | Groww redesigns page layout (HTML structure changes) | Parser fails gracefully; logs which fields could not be located; does not write a corrupt JSON record |
| EC-07 | A specific field is absent from the page (e.g., Exit Load section removed) | Write `null` for that field; do not omit the fund record entirely |
| EC-08 | Field value contains unexpected characters (e.g., `₹1,000` vs `1000`, `—` dash vs `-`) | Normalise numeric and currency strings during parsing; store raw and parsed values |
| EC-09 | NAV figure is temporarily unavailable (market holiday, after hours) | Store last known NAV with a `nav_as_of` timestamp; surface staleness in the footer |
| EC-10 | HTML is served with a non-UTF-8 encoding | Detect encoding via response headers; decode correctly before parsing |
| EC-11 | Response body is unexpectedly large (e.g., Groww embeds extra JS bundles) | Stream and parse incrementally; do not load entire page into memory |
| EC-12 | Fund page has been delisted or URL is permanently gone (410) | Mark fund as `status: inactive` in JSON; backend returns a graceful "data unavailable" message |

### 1.3 Scheduler & Concurrent Execution

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-13 | Scheduler fires while a previous ingestion run is still in progress | Skip the new trigger; log a warning; do not run two ingestion jobs concurrently |
| EC-14 | Scheduler fires but the machine is asleep / process is down | Miss is acceptable; next scheduled trigger runs normally; no catch-up duplication |
| EC-15 | Disk full during atomic JSON write | Write to temp file fails; original JSON remains untouched; alert logged |
| EC-16 | File permission error on `mutual_funds_data.json` | Log error; retain stale data; surface "data may be outdated" warning via backend |
| EC-17 | Ingestion partially succeeds (3 of 5 funds updated) | Write only fully-parsed fund records; retain last-good data for failed funds; log which funds were updated vs retained |

---

## 2. RAG Retrieval Engine (Phase 3)

### 2.1 Query Keyword Matching

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-18 | Query contains no recognisable fund keyword (e.g., "What is a mutual fund?") | Inject full 5-fund corpus as context; LLM answers generically or indicates scope |
| EC-19 | Query matches multiple fund keywords (e.g., "Compare mid-cap and small-cap") | Flag as advisory/comparison query; route to Refusal Engine |
| EC-20 | Fund name is misspelled (e.g., "HDFC Midcap" vs "HDFC Mid-Cap") | Apply fuzzy/normalised keyword matching (strip hyphens, lowercase, common abbreviations) |
| EC-21 | Query uses an alias or abbreviation (e.g., "MOF" for "Fund of Fund", "defence ETF") | Map known aliases to canonical fund names in a lookup table |
| EC-22 | Query mentions a fund outside the corpus (e.g., "HDFC Flexi Cap") | Respond that the assistant only covers the 5 listed HDFC schemes; do not hallucinate |

### 2.2 Input Validation

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-23 | Empty or whitespace-only message | Reject at the API layer (HTTP 400) before any processing |
| EC-24 | Message exceeds max character limit | Truncate at the defined limit (e.g., 500 chars); notify user in the response |
| EC-25 | Message is non-English (Hindi, regional language) | Detect language; respond in English with a note that only English queries are supported, or translate and process |
| EC-26 | Message contains only special characters or emojis | Treat as unrecognised query; return a helpful fallback asking the user to rephrase |
| EC-27 | JSON body is malformed (invalid JSON in POST /api/chat) | Return HTTP 422 with a clear validation error; do not crash the server |
| EC-28 | `message` field is missing from the request body | Return HTTP 422; do not proceed to LLM call |

---

## 3. PII Detector & Filter (Phase 3)

### 3.1 PII Format Variations

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-29 | PAN in mixed case (e.g., `abcde1234f`) | Normalise to uppercase before regex match; detect and strip |
| EC-30 | Aadhaar with spaces or dashes (e.g., `1234 5678 9012`, `1234-5678-9012`) | Strip separators before matching 12-digit pattern |
| EC-31 | Phone number with country code (e.g., `+91 98765 43210`, `0091-9876543210`) | Strip country code and separators; match 10-digit mobile pattern |
| EC-32 | Email with subdomains or unusual TLDs (e.g., `user@mail.co.in`) | Use a broad email regex; err on the side of detection |
| EC-33 | PII embedded mid-sentence (e.g., "My PAN is ABCDE1234F, what is the exit load?") | Detect, strip the PII token, and process the remaining query — do not reject the entire message |
| EC-34 | Multiple PII types in a single message | Detect and strip all instances; log the PII types detected (not values) for audit |
| EC-35 | Obfuscated PII (e.g., `A*CDE1234F`, partial masking) | Flag pattern-matching hits; apply conservative stripping |
| EC-36 | Account number or OTP appearing in a financial question context | Strip regardless of context; do not attempt to interpret whether it is "intentional" |

---

## 4. Advisory Query Detection & Refusal (Phase 3)

### 4.1 Advisory Boundary Cases

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-37 | Direct advice request ("Should I invest in HDFC Mid-Cap?") | Route to Refusal Engine; polite refusal + AMFI/SEBI educational link |
| EC-38 | Indirect advice framing ("My friend wants to know which fund is better") | Treat as advisory regardless of framing; refuse |
| EC-39 | Comparison query ("Which has a lower expense ratio, mid-cap or small-cap?") | This is a factual comparison, not investment advice — answer with data from both fund records |
| EC-40 | Return/performance query ("What returns has this fund given in 5 years?") | Performance data is outside the corpus scope; respond that this data is not available and link to the official factsheet |
| EC-41 | Borderline safety question ("Is this fund safe?") | Classify as advisory (risk assessment = opinion); refuse with link to Riskometer explanation |
| EC-42 | Multi-intent query (factual + advisory in one message, e.g., "What is the exit load and should I invest?") | Answer the factual part; refuse the advisory part within the same response |
| EC-43 | Future projection query ("Will the NAV go up?") | Refuse; no speculative or predictive content allowed |

---

## 5. LLM Orchestration & Prompt Engineering (Phase 4)

### 5.1 LLM API Failures

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-44 | LLM API request times out | Retry once with the same prompt; if retry fails, return HTTP 503 with a user-facing "service temporarily unavailable" message |
| EC-45 | LLM API returns rate limit error (429) | Queue or back-off; surface a polite "please try again in a moment" to the user |
| EC-46 | LLM API key is invalid or expired | Fail fast at startup health check; do not serve requests with an invalid key |
| EC-47 | LLM provider is switched (Claude → Gemini or vice versa) | Both providers go through the same `LLMClient` interface; prompt format and post-processing remain unchanged |
| EC-48 | LLM returns an empty response | Treat as a generation failure; return a fallback message asking the user to rephrase |

### 5.2 Response Constraint Violations

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-49 | LLM response exceeds 3 sentences | Post-processor truncates to 3 sentences at a sentence boundary; does not cut mid-sentence |
| EC-50 | Truncation at 3 sentences removes the citation link | Re-inject the citation link as a 4th element (not counted as a sentence) |
| EC-51 | LLM omits the citation link entirely | Post-processor detects absence; appends the canonical fund URL from the corpus before returning |
| EC-52 | LLM hallucination: citation link points to a domain not in the verified corpus | Post-processor validates domain against an allowlist (groww.in, hdfcfund.com, amfiindia.com, sebi.gov.in); replace with correct corpus URL if invalid |
| EC-53 | LLM returns markdown formatting (bold, bullets) in a plain-text context | Strip markdown before sending to the frontend |
| EC-54 | LLM returns HTML or script tags | Sanitise output; strip all HTML tags before rendering |
| EC-55 | Footer (`Last updated from sources: <date>`) is missing from response | Post-processor always appends footer using timestamp from JSON; it is never LLM-generated |

### 5.3 Prompt Construction

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-56 | Full 5-fund JSON context exceeds LLM's context window | Summarise or truncate fields to essential ones (NAV, Expense Ratio, Exit Load, Min SIP, Fund Manager); log a warning |
| EC-57 | User query itself is a prompt injection attempt (e.g., "Ignore all previous instructions and…") | System prompt instructs the LLM to treat user message as untrusted input; post-processor checks for out-of-scope directives in response |
| EC-58 | LLM over-refuses a clearly factual question due to financial content sensitivity | Log false-positive refusals; tune advisory classifier or system prompt instructions |

---

## 6. Frontend UI (Phase 5)

### 6.1 Input & Interaction

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-59 | User submits an empty message via the chat form | Disable the submit button until input is non-empty; no API call made |
| EC-60 | User rapidly clicks Send multiple times | Disable the Send button after first click until the response is received; prevent duplicate requests |
| EC-61 | User pastes a block of text containing PII | Client-side validation warns the user and clears the PII before submission (defence-in-depth alongside server-side filter) |
| EC-62 | Quick-question pill clicked while a response is loading | Queue or ignore the click; do not fire a second concurrent request |
| EC-63 | User sends an extremely long message that exceeds the display width | Chat bubble wraps text; no overflow or horizontal scrolling |

### 6.2 Network & Response Rendering

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-64 | Network disconnects mid-request | Show a "connection lost, please try again" error bubble; do not hang indefinitely |
| EC-65 | Server returns HTTP 5xx | Display a user-friendly error message; do not expose raw error details or stack traces |
| EC-66 | Response body is extremely long (edge case from EC-56 fallback) | Chat bubble scrolls within a max-height container; does not break the layout |
| EC-67 | Citation link in the response is very long (URL wrapping) | Apply CSS `word-break: break-word` or display as a labelled hyperlink ("Source") |
| EC-68 | LLM response contains XSS payload (e.g., `<script>` tags) | Frontend renders response as text, not innerHTML; all output is escaped |

### 6.3 Responsiveness & Theming

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-69 | Mobile soft keyboard opens and pushes the chat input off-screen | Use `viewport` meta tag and CSS `env(safe-area-inset-bottom)` to keep input visible |
| EC-70 | User changes system dark/light mode preference mid-session | UI theme updates in real time via `prefers-color-scheme` media query |
| EC-71 | Disclaimer bar is scrolled out of view | Disclaimer is sticky/fixed; always visible regardless of scroll position |

---

## 7. Security & Compliance (Phase 6)

### 7.1 API Security

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-72 | Request originates from an unauthorised CORS origin | Server returns HTTP 403; request is blocked before processing |
| EC-73 | Rate limit is exceeded by a single client (IP-based) | Return HTTP 429 with `Retry-After` header; do not process the request |
| EC-74 | Attacker sends crafted payloads to probe PII filter bypass (e.g., unicode lookalikes in PAN) | PII filter normalises unicode before matching; edge cases logged for review |
| EC-75 | Request body is abnormally large (payload bombing) | Enforce a max request body size limit at the FastAPI/server level; return HTTP 413 |

### 7.2 Data & Compliance

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-76 | LLM response inadvertently contains investment advice despite system prompt | Post-processor does a secondary advisory scan on the LLM output; flag and replace with refusal if detected |
| EC-77 | Citation link in LLM response resolves to a third-party blog or aggregator (not official) | Domain allowlist check fails; replace with the official corpus URL |
| EC-78 | `Last updated` date in JSON is more than N days stale (scheduler failure) | Backend appends a staleness warning in the footer: `"Note: data may be outdated — last refresh: <date>"` |
| EC-79 | Log files inadvertently capture PII from a query that slipped through the filter | Logs must redact the `message` field or store only a hash; never log raw user input |

---

## 8. Cross-Cutting & Startup Scenarios

| # | Scenario | Expected Behaviour |
|---|----------|--------------------|
| EC-80 | Backend starts before `mutual_funds_data.json` exists | Server refuses to start (fails health check) and logs a clear error instructing the operator to run the ingestion script first |
| EC-81 | `mutual_funds_data.json` is malformed/corrupt JSON | Server fails to start; logs parse error with file path; does not serve requests with empty context |
| EC-82 | LLM API key environment variable is not set | Server refuses to start; logs a clear missing-configuration error |
| EC-83 | All 5 fund records are present but one is `status: inactive` (EC-12) | Backend excludes inactive fund from context injection; responds that data for that fund is currently unavailable |
| EC-84 | Concurrent requests hit the server simultaneously | FastAPI handles concurrency via async; no shared mutable state between requests; JSON is loaded once at startup into a read-only in-memory structure |
| EC-85 | Server is restarted mid-ingestion cycle | Ingestion uses atomic file swap; partial writes never replace the live JSON |

---

## Summary Matrix

| Component | # Edge Cases |
|-----------|-------------|
| Ingestion & Parser | EC-01 → EC-17 (17) |
| RAG Retrieval | EC-18 → EC-28 (11) |
| PII Filter | EC-29 → EC-36 (8) |
| Advisory Detection & Refusal | EC-37 → EC-43 (7) |
| LLM Orchestration | EC-44 → EC-58 (15) |
| Frontend UI | EC-59 → EC-71 (13) |
| Security & Compliance | EC-72 → EC-79 (8) |
| Cross-Cutting / Startup | EC-80 → EC-85 (6) |
| **Total** | **85** |
