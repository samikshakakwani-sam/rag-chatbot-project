# Deployment Plan — FundFacts AI (HDFC Fund FAQ Assistant)

> Strategy & phased plan for taking the app from local to production.
> For the copy-paste command runbook, see **[`DEPLOY.md`](../DEPLOY.md)**.

---

## 1. Goal & shape

Ship the app as a **single Docker container**: the FastAPI backend serves the JSON
API **and** the static frontend (chat UI, compliance page, v1) same-origin. There is
exactly one deployable service — no separate frontend host, no external database.

What is **baked into the image at build time** (so cold-start is fast and offline-safe):
- The BGE-small embedding model (`BAAI/bge-small-en-v1.5`).
- The persistent ChromaDB vector store (rebuilt from `data/chunks.json`).
- The 45-chunk corpus.

What is **injected at runtime** (never in the image):
- `GROQ_API_KEY` — from the platform secret store.

---

## 2. Target topology

```
                      ┌────────────────────────────────────────────┐
   Public users  ──►  │  PaaS edge (HTTPS, TLS termination)         │
                      │     │                                       │
                      │     ▼                                       │
                      │  Container : uvicorn → FastAPI              │
                      │     • GET /  /compliance  /v1   (UI)        │
                      │     • POST /api/chat  (rate-limited)        │
                      │     • in-memory: chunks + Chroma + BGE      │
                      │     └── outbound HTTPS ──► Groq API         │
                      └────────────────────────────────────────────┘
```

- **Stateless**: no user data persisted; conversations are anonymous. Horizontal
  scaling is possible, with the caveat that the rate limiter is per-instance (§9).
- **Resource floor**: ~2 GB RAM (PyTorch + model), 1 vCPU. Build needs ~3–4 GB disk.

---

## 3. Pre-deployment checklist (gate — do not deploy until all ✅)

| # | Item | Status |
|---|------|--------|
| 1 | 🔴 **Rotate the Groq key** (prior keys shared in plaintext = compromised) | ☐ |
| 2 | **Set a Groq spend limit** in the console (public endpoint = quota risk) | ☐ |
| 3 | Key stored **only** as a platform secret; never committed (`.env` is gitignored) | ☐ |
| 4 | `pytest` green (47/47) | ☐ |
| 5 | Decide access posture: open vs. shared-token (§8) | ☐ |
| 6 | Confirm repo has **no secret** (`git grep gsk_` → empty) | ☐ |
| 7 | Pick platform + instance size ≥ 2 GB RAM | ☐ |

---

## 4. Environment & secrets matrix

| Var | Where | Required | Default | Notes |
|---|---|---|---|---|
| `GROQ_API_KEY` | **secret** | yes (for LLM) | — | Without it, app falls back to deterministic stub |
| `LLM_PROVIDER` | env | no | `groq` (Dockerfile) | `stub` for a key-free demo |
| `GROQ_MODEL` | env | no | `llama-3.3-70b-versatile` | |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` | env | no | `30` / `60` | Lower for public (e.g. `15`/`60`) |
| `ACCESS_TOKEN` *(optional, see §8)* | secret | no | unset | If added, gates `/api/chat` |
| `PORT` | env | platform-set | `7860` | Honoured by the container CMD |
| `CORS_ORIGINS` | env | no | localhost set | **Not needed** for same-origin UI |

---

## 5. Platform decision

| Platform | Fit | RAM (free) | Builds image? | Best for |
|---|---|---|---|---|
| **Hugging Face Spaces** ⭐ | High | 16 GB (free CPU) | Yes (remote) | Quickest free public demo; account already exists (TglobalAI) |
| **Render** | High | 512 MB free → too small; use ≥2 GB paid | Yes (remote) | Git-push CI/CD, custom domains |
| **Fly.io** | Medium | needs card | Yes | Global edge, fine-grained VM control |
| **Cloud Run / VPS** | Medium | n/a | Local/registry | Full control, needs Docker locally |

**Recommendation: Hugging Face Spaces (Docker SDK)** for the first public release — free
tier comfortably fits the model, builds remotely (no local Docker needed), and the
account is already in place. Promote to Render/Fly with a custom domain later if needed.

---

## 6. Phased rollout

### Phase D1 — Prepare (done)
- ✅ `Dockerfile`, `.dockerignore`, `DEPLOY.md`, pinned `requirements.txt`.
- ✅ Verified COPY sources present and the prod bind (`0.0.0.0:$PORT`) serves health + chat.
- ✅ Code pushed to GitHub (`samikshakakwani-sam/rag-chatbot-project`), secret-audited.

### Phase D2 — Provision *(user-only steps)*
- Create the Space / service (account, ToS acceptance).
- Add `GROQ_API_KEY` (rotated) as a secret; set `RATE_LIMIT_MAX`.

### Phase D3 — Build & deploy
- Platform builds the image from the `Dockerfile` (~few min for torch).
- First boot: model + Chroma already baked → engine ready in seconds.

### Phase D4 — Verify (smoke tests, §10)
- `/health` ok; factual query cited; advisory refused; PII rejected.

### Phase D5 — Harden
- Confirm HTTPS enforced; rate limit tuned; Groq spend cap active.
- (Optional) custom domain + `ACCESS_TOKEN`.

### Phase D6 — Operate
- Monitor logs/usage; plan corpus refresh (§11).

---

## 7. Rollback strategy

- **Image-based platforms** keep prior builds: roll back to the last good build/commit
  in one click (HF Spaces → "Factory rebuild" / previous commit; Render → "Rollback").
- Because the corpus is baked into the image, a rollback also restores the prior corpus
  snapshot — deterministic and safe.
- Git is the source of truth; every deploy maps to a commit SHA.

---

## 8. Access control (decision)

The endpoint is a public LLM proxy → spend/abuse risk. Options:
1. **Open** (default) — relies on per-IP rate limit + Groq spend cap. Simplest; matches
   "for everyone to use." Acceptable **only** with a hard Groq spend limit.
2. **Shared token** — add `ACCESS_TOKEN`; `/api/chat` requires a matching header. Not yet
   implemented (would be a small `main.py` addition); contradicts a fully-open demo but
   protects quota.
3. **Tighter rate limit** — lower `RATE_LIMIT_MAX` (e.g. 10/min) as a middle ground.

> Recommendation for a public demo: **open + low rate limit + Groq spend cap.** Add a
> token only if abuse appears.

---

## 9. Scaling & cost

- **Single instance** handles a demo comfortably (retrieval is in-memory over 45 vectors;
  latency is dominated by the Groq call).
- **Horizontal scaling caveat**: the rate limiter is **in-memory per instance** — N
  instances ⇒ effective limit ×N, and counters reset on redeploy. For real multi-instance
  enforcement, move to a shared store (Redis) behind the same `RateLimiter.allow()` API.
- **Cost drivers**: Groq tokens (per request) and instance RAM/uptime. The embedding model
  is local/free; no vector-DB service fees.

---

## 10. Post-deploy verification

```bash
BASE=https://YOUR_URL
curl -s $BASE/health                       # {"status":"ok","funds":5,"semantic":true,...}
curl -s -X POST $BASE/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"what is the expense ratio of HDFC Mid-Cap?"}'   # cited 0.73% answer
curl -s -X POST $BASE/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"which fund is best?"}'                          # refused (advisory) + AMFI link
curl -s -X POST $BASE/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"my PAN is ABCDE1234F"}'                         # refused (pii), not echoed
```

Acceptance: factual → cited; advisory → refused w/ educational link; PII → rejected;
every answer ≤3 sentences with a `groww.in`/AMFI/SEBI citation.

---

## 11. Maintenance — corpus freshness in production

The deployed corpus is a **snapshot baked at build time**; PaaS filesystems are ephemeral,
so the daily in-process scheduler is **not** the right model in a single web container.
Choose one:

- **A. Redeploy to refresh (simplest).** Re-run ingestion locally (or in CI), commit the
  updated `data/chunks.json`, and redeploy → image rebuild re-embeds. Cadence: as needed.
- **B. Scheduled rebuild.** A platform cron (Render Cron Job / Fly scheduled machine / GitHub
  Action) runs `python -m ingestion.ingest`, commits the refreshed corpus, and triggers a deploy.
- **C. Persistent volume + side worker.** Mount a volume for `data/`, run a separate worker
  running `ingestion/scheduler.py`, point the web app at the shared volume. Most "live", most
  moving parts.

> Recommendation: **A or B.** The corpus changes slowly (NAV/TER), so a daily/weekly rebuild
> is sufficient and keeps the web tier simple and stateless.

---

## 12. Production security & compliance

- **Secrets**: key only in platform secret store; image and repo are secret-free (audited).
- **Transport**: PaaS terminates TLS; force HTTPS.
- **CORS**: locked (no wildcard); same-origin UI needs none.
- **PII**: rejected pre-processing; raw PII never logged/stored/echoed; conversations stateless.
- **Advisory**: recommendation/comparison/return-prediction refused with AMFI/SEBI link.
- **Citations**: enforced to `groww.in` / `amfiindia.com` / `sebi.gov.in`.
- **Abuse**: per-IP rate limit → 429; add Groq spend cap; optional access token.

---

## 13. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Compromised Groq key abused | High if unrotated | **Rotate**; spend cap; key only in secret store |
| Public quota drain | Medium | Low rate limit; spend cap; optional token |
| Free-tier RAM too small (torch OOM) | Medium (Render free) | Use ≥2 GB instance / HF free CPU (16 GB) |
| Stale corpus | Low | Scheduled rebuild (§11); `last_updated` shown in UI |
| Multi-instance rate-limit dilution | Low (demo) | Single instance, or Redis-backed limiter |
| Cold-start latency | Low | Model + Chroma baked into image |

---

## 14. Status

- **Phase D1 (Prepare): ✅ complete** — artifacts built, verified, pushed.
- **Phase D2–D6: pending user action** — provision, add rotated secret, deploy, verify.
