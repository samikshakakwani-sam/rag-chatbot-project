# Deploying FundFacts AI

The app is a single Docker image: the FastAPI backend serves the API **and** the
frontend (chat UI + compliance page) same-origin, so there is exactly one service
to deploy. No separate frontend host is needed.

---

## 🔴 Before you deploy — do these first

1. **Rotate the Groq API key.** The key currently in `.env` was shared in plaintext
   and must be considered compromised. Generate a new one at
   <https://console.groq.com/keys> and use it **only** as a platform secret (never
   commit it, never bake it into the image).
2. **Set a Groq spend limit.** A public, no-auth endpoint means anyone can use it and
   spend your quota. Set a hard cap in the Groq console.
3. The image **never contains the key** — `.env` is excluded via `.dockerignore`. At
   runtime the app reads `GROQ_API_KEY` from the platform's environment. If it's
   missing, the app still runs but falls back to the deterministic stub (verbatim
   facts, no LLM phrasing).

---

## Required runtime environment variables

| Var | Value | Notes |
|---|---|---|
| `GROQ_API_KEY` | *your new key* | **Set as a secret.** Required for LLM answers. |
| `LLM_PROVIDER` | `groq` | Already defaulted in the Dockerfile. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Optional override. |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` | e.g. `20` / `60` | Per-IP quota on `/api/chat`. Lower for public. |

> CORS needs no configuration for the deployed UI — it's served same-origin, so the
> browser never makes a cross-origin request. Only set `CORS_ORIGINS` if you host the
> frontend on a *different* domain than the API.

---

## Option A — Hugging Face Spaces  *(recommended; free CPU tier fits the model)*

You're signed in as **TglobalAI**. Steps **you** perform (account + secret + publish):

1. Create a new Space → <https://huggingface.co/new-space>
   - **SDK:** Docker → *Blank*
   - **Hardware:** CPU basic (free) is enough (16 GB RAM).
2. Add the Space's `README.md` front-matter (HF reads this to build the Docker image
   and route the port). Put this at the very top of the Space `README.md`:

   ```yaml
   ---
   title: FundFacts AI
   emoji: 🏦
   colorFrom: green
   colorTo: blue
   sdk: docker
   app_port: 7860
   pinned: false
   ---
   ```
3. Push this repo to the Space (it builds from the `Dockerfile` automatically):

   ```bash
   git init && git add -A && git commit -m "FundFacts AI"
   git remote add space https://huggingface.co/spaces/TglobalAI/fundfacts-ai
   git push space main
   ```
4. In the Space → **Settings → Variables and secrets** → add a **secret**
   `GROQ_API_KEY` = your new key. (This is the step I can't do for you — entering a
   key into a dashboard.)
5. The Space builds (~a few minutes for torch) and goes live at
   `https://tglobalai-fundfacts-ai.hf.space`.

---

## Option B — Render (Docker web service)

1. Push this repo to GitHub.
2. Render → **New → Web Service** → connect the repo → it detects the `Dockerfile`.
3. **Instance type:** pick one with ≥ 2 GB RAM (the free 512 MB tier is too small for
   PyTorch). Render injects `$PORT` automatically — the Dockerfile already honours it.
4. **Environment → Add secret** `GROQ_API_KEY`.
5. Deploy. Live at `https://<service>.onrender.com`.

A `render.yaml` (optional, for blueprint deploys):

```yaml
services:
  - type: web
    name: fundfacts-ai
    runtime: docker
    plan: standard            # ≥2GB RAM
    envVars:
      - key: GROQ_API_KEY
        sync: false           # set in dashboard as a secret
      - key: RATE_LIMIT_MAX
        value: "20"
```

---

## Option C — Fly.io

Requires a card on file. After `flyctl auth login` (your credentials):

```bash
fly launch --no-deploy            # generates fly.toml; set internal_port = 7860
fly secrets set GROQ_API_KEY=your_new_key
fly deploy
```

Minimum `fly.toml` bits:

```toml
[http_service]
  internal_port = 7860
  force_https = true
[[vm]]
  memory = "2gb"
```

---

## Option D — Any Docker host (VPS, Cloud Run, etc.)

```bash
docker build -t fundfacts-ai .
docker run -p 8080:7860 -e GROQ_API_KEY=your_new_key fundfacts-ai
# → http://localhost:8080
```

For Google Cloud Run: push the image to a registry, deploy with `--memory 2Gi` and a
secret env var for `GROQ_API_KEY`.

---

## What only you can do (deployment boundaries)

I prepared everything up to these — they require your account/credentials/consent:
- Create the hosting account / log in.
- Enter `GROQ_API_KEY` into the platform's secret manager.
- Accept the platform's Terms of Service.
- Trigger the publish (push / deploy).

## Post-deploy smoke test

```bash
curl https://YOUR_URL/health
curl -X POST https://YOUR_URL/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"what is the expense ratio of HDFC Mid-Cap?"}'
```

Expect `{"reply": "...0.73%...", "citation": "https://groww.in/...", ...}`.
