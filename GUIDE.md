# Dialpad Webhook Service — Setup, Testing & Production Guide

## Architecture Overview

```
Dialpad Cloud  ──webhook POST──►  FastAPI on Render  ──►  PostgreSQL (Render)
                                       │
                                       ├── /webhooks/call   (receives events)
                                       ├── /api/calls       (query stored data)
                                       ├── /api/transcripts (query transcripts)
                                       └── /health          (monitoring)
                                       │
                                  On hangup event:
                                       └── GET /transcripts/{call_id}  ──► Dialpad API
```

**Flow:**
1. Dialpad sends a call event (hangup, recording, call_transcription) to your Render webhook URL
2. The service verifies the JWT signature (if secret is configured)
3. On `hangup` → stores the call log in PostgreSQL, then asynchronously fetches the transcript
4. On `call_transcription` → fetches the transcript (it's now ready)
5. On `recording` → updates the call log with the recording URL

---

## Phase 1: Local Testing (No Dialpad Needed)

### Step 1: Start the services

```bash
cd dialpad-webhook-service

# Create your .env file
cp .env.example .env
# Edit .env and add your DIALPAD_API_KEY

# Start PostgreSQL + app with Docker
docker compose up -d

# Verify it's running
curl http://localhost:8000/health
```

You should see: `{"status": "ok", "database": "ok", ...}`

### Step 2: Send test events

```bash
# Send simulated Dialpad events to your local service
python scripts/test_webhook.py
```

This sends 5 sample events (outbound hangup, inbound hangup, missed call, recording, transcript ready) using real call IDs from your CSV data. It then queries the API to verify everything was stored.

### Step 3: Verify stored data

```bash
# List all calls
curl http://localhost:8000/api/calls | python -m json.tool

# Get a specific call with transcript
curl http://localhost:8000/api/calls/5400430676418560 | python -m json.tool

# Check stats
curl http://localhost:8000/api/stats | python -m json.tool
```

---

## Phase 2: Deploy to Render & Test with Real Dialpad Events

### Step 1: Push to GitHub

```bash
cd dialpad-webhook-service
git init
git add .
git commit -m "Dialpad webhook service — initial commit"
git remote add origin https://github.com/YOUR-ORG/dialpad-webhook-service.git
git push -u origin main
```

### Step 2: Deploy on Render (Blueprint — fastest)

1. Go to https://dashboard.render.com
2. Click **New** → **Blueprint**
3. Connect your GitHub repo
4. Render detects `render.yaml` and creates both the **web service** and **PostgreSQL database** automatically
5. After creation, go to the web service → **Environment** tab
6. Set these environment variables:
   - `DIALPAD_API_KEY` = your Dialpad sandbox API key
   - `DIALPAD_WEBHOOK_SECRET` = generate one with:
     ```bash
     python -c "import secrets; print(secrets.token_urlsafe(32))"
     ```
7. Click **Save Changes** — Render redeploys automatically

Your service URL will be: `https://dialpad-webhook.onrender.com`

### Step 2 (Alternative): Deploy manually on Render

If you prefer not to use the Blueprint:

**Create the database:**
1. Render Dashboard → **New** → **PostgreSQL**
2. Name: `dialpad-db`, Database: `dialpad_calls`, User: `dialpad`
3. Plan: Free (for testing) or Starter ($7/mo for production)
4. Copy the **Internal Database URL** after creation

**Create the web service:**
1. Render Dashboard → **New** → **Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Environment variables:
   - `DATABASE_URL` = the Internal Database URL from above
   - `DIALPAD_API_KEY` = your key
   - `DIALPAD_WEBHOOK_SECRET` = your generated secret
   - `DIALPAD_API_BASE_URL` = `https://sandbox.dialpad.com/api/v2`
   - `LOG_LEVEL` = `info`
5. Health Check Path: `/health`
6. Click **Create Web Service**

### Step 3: Verify deployment

```bash
# Replace with your actual Render URL
RENDER_URL=https://dialpad-webhook.onrender.com

# Health check
curl $RENDER_URL/health

# Send test events to Render
python scripts/test_webhook.py --url $RENDER_URL
```

### Step 4: Register webhook with Dialpad Sandbox

```bash
# Point Dialpad at your Render service
python scripts/register_webhook.py \
  --url https://dialpad-webhook.onrender.com/webhooks/call \
  --secret "YOUR_SAME_WEBHOOK_SECRET"
```

**Important:** The `--secret` value here MUST match the `DIALPAD_WEBHOOK_SECRET` env var on Render. They're the same key — Dialpad uses it to sign, your service uses it to verify.

Save the webhook ID and subscription ID that are printed.

### Step 5: Make test calls

1. Log into your Dialpad sandbox account
2. Make a few test calls (inbound and outbound)
3. Check that data is arriving:

```bash
curl $RENDER_URL/api/calls | python -m json.tool
curl $RENDER_URL/api/stats | python -m json.tool
```

4. Check a specific call's transcript:
```bash
curl $RENDER_URL/api/calls/{call_id} | python -m json.tool
```

The transcript should show `"status": "success"` with the full text and moments array.

---

## Phase 3: Move to Production

### Pre-production Checklist

- [ ] **API Key scopes**: Ensure your production API key has these scopes:
  - `recordings_export` — to receive recording URLs in events
  - `call_transcription` — to fetch AI transcripts
  - Your key already works if you can fetch transcripts via `GET /transcripts/{call_id}`

- [ ] **Webhook secret**: Use a strong random secret (32+ chars) for JWT verification
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- [ ] **Upgrade Render plan**: Free tier spins down after inactivity (30s cold start). Use **Starter** ($7/mo) for production to keep the service always on.

- [ ] **Upgrade PostgreSQL**: Free tier expires after 90 days. Use **Starter** ($7/mo) or **Standard** for persistence.

### Switch to Production Dialpad API

1. On Render, update the environment variable:
   - `DIALPAD_API_BASE_URL` → `https://dialpad.com/api/v2`
   - `DIALPAD_API_KEY` → your **production** API key
2. Register a new webhook pointing to the same Render URL but using production:

```bash
python scripts/register_webhook.py \
  --url https://dialpad-webhook.onrender.com/webhooks/call \
  --secret "YOUR_WEBHOOK_SECRET" \
  --production
```

### Render Free Tier Gotcha

The free web service sleeps after 15 minutes of inactivity. When Dialpad sends a webhook to a sleeping service, Render wakes it up (~30 seconds). Dialpad may retry, so the idempotency logic handles this. But for production, upgrade to **Starter** to avoid missed events during cold starts.

### Monitoring

- **Health endpoint**: `GET /health` — returns database status
- **Stats endpoint**: `GET /api/stats` — total calls, transcripts, pending events
- **Render dashboard**: View logs, metrics, and deploy history at https://dashboard.render.com
- Set up Render **Health Check Notifications** (built-in) or UptimeRobot for external monitoring

---

## API Reference (Your Service)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhooks/call` | Receives Dialpad call events |
| `POST` | `/webhooks/sms` | Receives Dialpad SMS events |
| `GET` | `/api/calls` | List calls (filters: direction, category, agent_email, date_from, date_to) |
| `GET` | `/api/calls/{call_id}` | Get call details + transcript |
| `GET` | `/api/transcripts/{call_id}` | Get transcript only |
| `GET` | `/api/stats` | Dashboard stats |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive Swagger UI |

### Query Examples

```bash
# Replace with your Render URL
BASE=https://dialpad-webhook.onrender.com

# Outbound calls only
curl "$BASE/api/calls?direction=outbound"

# Missed calls
curl "$BASE/api/calls?category=missed"

# Calls by a specific agent
curl "$BASE/api/calls?agent_email=aleksandar_malinic@gojobengine.com"

# Date range
curl "$BASE/api/calls?date_from=2026-03-05&date_to=2026-03-06"
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No events arriving | Check Render service is running (not sleeping), webhook URL is correct, Dialpad subscription is active |
| JWT decode error | Verify `DIALPAD_WEBHOOK_SECRET` on Render matches the `--secret` used in `register_webhook.py` |
| Transcripts show "not_available" | The call may be too short or AI transcription isn't enabled for that user/call center |
| Duplicate call records | The service has built-in idempotency — duplicates are detected and skipped |
| Rate limit errors on transcripts | Transcripts are fetched with a 5-second delay; for high volume, consider adding a task queue |
| Database connection errors | Check DATABASE_URL on Render, ensure the PostgreSQL service is running |
| Render cold starts (free tier) | Upgrade to Starter plan ($7/mo) to keep the service always on |
| `asyncpg` connection error with Render DB URL | Already handled — `config.py` auto-converts `postgres://` to `postgresql+asyncpg://` |
