"""
Dialpad Webhook Service — FastAPI Application

Receives Dialpad call/SMS/agent events via webhooks,
stores call logs in PostgreSQL, and fetches AI transcripts.
"""
import logging
import jwt
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text

from app.config import get_settings
from app.database import get_db, init_db, engine
from app.models import CallLog, CallTranscript, WebhookEvent
from app.webhook_handler import process_call_event
from app.dialpad_client import dialpad_client

logger = logging.getLogger(__name__)
settings = get_settings()

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Dialpad Webhook Service...")
    await init_db()
    logger.info("Database tables initialized")
    yield
    logger.info("Shutting down...")
    await dialpad_client.close()
    await engine.dispose()


app = FastAPI(
    title="Dialpad Webhook Service",
    description="Receives Dialpad call events, stores call logs, and fetches transcripts.",
    version="1.0.0",
    lifespan=lifespan,
)


# ─── JWT Verification ───────────────────────────────────────────────

def verify_webhook_payload(raw_body: bytes) -> dict:
    """
    Decode and verify a Dialpad webhook payload.

    If a secret is configured, payloads arrive as JWT (HS256).
    If no secret, payloads are plain JSON.
    """
    secret = settings.dialpad_webhook_secret

    if secret:
        try:
            token = raw_body.decode("utf-8").strip()
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            return payload
        except jwt.InvalidSignatureError:
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Webhook token expired")
        except jwt.DecodeError:
            raise HTTPException(status_code=400, detail="Invalid JWT token")
    else:
        # No secret — plain JSON (development mode)
        import json
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")


# ─── Webhook Endpoints ──────────────────────────────────────────────

@app.post("/webhooks/call", status_code=200)
async def handle_call_event(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receive Dialpad call events (ringing, connected, hangup, recording, etc.).

    On hangup: stores the call log and triggers transcript fetch.
    On call_transcription: triggers transcript fetch.
    On recording: updates the recording URL.
    """
    raw_body = await request.body()
    payload = verify_webhook_payload(raw_body)

    call_id = payload.get("call_id", "unknown")
    state = payload.get("state", "unknown")
    logger.info(f"Call event received: call_id={call_id} state={state}")

    try:
        await process_call_event(db, payload)
    except Exception as e:
        logger.error(f"Error processing call event {call_id}: {e}")
        # Still return 200 to prevent Dialpad retries on app errors
        # The event is stored in webhook_events for later reprocessing

    return {"status": "ok", "call_id": call_id, "state": state}


@app.post("/webhooks/sms", status_code=200)
async def handle_sms_event(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive Dialpad SMS events. Stored as raw events for now."""
    raw_body = await request.body()
    payload = verify_webhook_payload(raw_body)

    logger.info(f"SMS event received: {payload.get('direction', 'unknown')}")

    event = WebhookEvent(
        event_type="sms",
        payload=payload,
        processed=True,
        received_at=datetime.utcnow(),
    )
    db.add(event)
    await db.commit()

    return {"status": "ok"}


# ─── API Endpoints (for querying stored data) ───────────────────────

@app.get("/api/calls")
async def list_calls(
    limit: int = 50,
    offset: int = 0,
    direction: str = None,
    category: str = None,
    agent_email: str = None,
    date_from: str = None,
    date_to: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Query stored call logs with optional filters."""
    query = select(CallLog).order_by(CallLog.date_started.desc())

    if direction:
        query = query.where(CallLog.direction == direction)
    if category:
        query = query.where(CallLog.category == category)
    if agent_email:
        query = query.where(CallLog.email == agent_email)
    if date_from:
        query = query.where(CallLog.date_started >= date_from)
    if date_to:
        query = query.where(CallLog.date_started <= date_to)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    calls = result.scalars().all()

    return {
        "count": len(calls),
        "calls": [
            {
                "call_id": c.call_id,
                "category": c.category,
                "direction": c.direction,
                "external_number": c.external_number,
                "internal_number": c.internal_number,
                "date_started": str(c.date_started) if c.date_started else None,
                "date_ended": str(c.date_ended) if c.date_ended else None,
                "talk_duration": c.talk_duration,
                "name": c.name,
                "email": c.email,
                "was_recorded": c.was_recorded,
                "categories": c.categories,
                "has_transcript": c.transcript is not None and c.transcript.fetch_status == "success" if c.transcript else False,
            }
            for c in calls
        ],
    }


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific call log with its transcript."""
    result = await db.execute(
        select(CallLog).where(CallLog.call_id == call_id)
    )
    call = result.scalars().first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Get transcript
    transcript_result = await db.execute(
        select(CallTranscript).where(CallTranscript.call_id == call_id)
    )
    transcript = transcript_result.scalars().first()

    return {
        "call": {
            "call_id": call.call_id,
            "category": call.category,
            "direction": call.direction,
            "external_number": call.external_number,
            "internal_number": call.internal_number,
            "date_started": str(call.date_started) if call.date_started else None,
            "date_connected": str(call.date_connected) if call.date_connected else None,
            "date_ended": str(call.date_ended) if call.date_ended else None,
            "talk_duration": call.talk_duration,
            "target_id": call.target_id,
            "target_type": call.target_type,
            "name": call.name,
            "email": call.email,
            "was_recorded": call.was_recorded,
            "recording_url": call.recording_url,
            "categories": call.categories,
            "raw_payload": call.raw_payload,
        },
        "transcript": {
            "status": transcript.fetch_status if transcript else "not_fetched",
            "summary": transcript.summary if transcript else None,
            "full_text": transcript.full_text if transcript else None,
            "moments": transcript.moments if transcript else None,
            "fetched_at": str(transcript.fetched_at) if transcript and transcript.fetched_at else None,
        } if transcript else None,
    }


@app.get("/api/transcripts/{call_id}")
async def get_transcript(call_id: str, db: AsyncSession = Depends(get_db)):
    """Get just the transcript for a call."""
    result = await db.execute(
        select(CallTranscript).where(CallTranscript.call_id == call_id)
    )
    transcript = result.scalars().first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return {
        "call_id": call_id,
        "status": transcript.fetch_status,
        "summary": transcript.summary,
        "full_text": transcript.full_text,
        "moments": transcript.moments,
        "fetched_at": str(transcript.fetched_at) if transcript.fetched_at else None,
    }


@app.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Quick stats overview."""
    total_calls = await db.execute(select(func.count(CallLog.id)))
    total_transcripts = await db.execute(
        select(func.count(CallTranscript.id)).where(CallTranscript.fetch_status == "success")
    )
    pending_events = await db.execute(
        select(func.count(WebhookEvent.id)).where(WebhookEvent.processed == False)
    )

    return {
        "total_calls": total_calls.scalar(),
        "total_transcripts": total_transcripts.scalar(),
        "pending_events": pending_events.scalar(),
    }


# ─── Health Check ────────────────────────────────────────────────────

@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check for monitoring."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        log_level=settings.log_level,
    )
