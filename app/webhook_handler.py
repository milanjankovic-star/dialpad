"""
Core webhook processing logic.
Parses Dialpad call events, stores them, and triggers transcript fetching.
"""
import logging
import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import CallLog, CallTranscript, WebhookEvent
from app.dialpad_client import dialpad_client

logger = logging.getLogger(__name__)


def parse_timestamp(value) -> Optional[datetime]:
    """Parse various timestamp formats from Dialpad."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        # Unix timestamp — Dialpad may send milliseconds or seconds
        try:
            if value > 1e12:
                value = value / 1000  # Convert milliseconds to seconds
            return datetime.utcfromtimestamp(value)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        # ISO format string like "2026-03-05 05:06:25.514052"
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def safe_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def safe_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


async def store_webhook_event(db: AsyncSession, event_type: str, payload: dict) -> WebhookEvent:
    """Store raw webhook event for idempotency tracking."""
    event = WebhookEvent(
        event_type=event_type,
        call_id=payload.get("call_id"),
        state=payload.get("state"),
        payload=payload,
        processed=False,
    )
    db.add(event)
    await db.flush()
    return event


async def is_duplicate_event(db: AsyncSession, call_id: str, state: str) -> bool:
    """Check if we already processed this call_id + state combo."""
    result = await db.execute(
        select(WebhookEvent).where(
            WebhookEvent.call_id == call_id,
            WebhookEvent.state == state,
            WebhookEvent.processed == True,
        )
    )
    return result.scalars().first() is not None


async def process_call_event(db: AsyncSession, payload: dict) -> Optional[CallLog]:
    """
    Process a Dialpad call webhook event.

    Maps webhook payload fields to our CallLog model which mirrors the CSV export format.
    Triggers transcript fetch for completed calls.
    """
    call_id = payload.get("call_id")
    state = payload.get("state")

    if not call_id:
        logger.warning("Received call event without call_id, skipping")
        return None

    # Dialpad sends call_id as int — ensure it's a string for DB storage
    call_id = str(call_id)
    payload["call_id"] = call_id

    # Store raw event first
    webhook_event = await store_webhook_event(db, "call", payload)

    # Idempotency check
    if await is_duplicate_event(db, call_id, state):
        logger.info(f"Duplicate event for call {call_id} state {state}, skipping")
        return None

    # For hangup events — store/update the call log
    if state == "hangup":
        call_log = await _upsert_call_log(db, payload)
        webhook_event.processed = True
        await db.commit()

        # Trigger async transcript fetch (don't block the webhook response)
        asyncio.create_task(_fetch_and_store_transcript(call_id))

        return call_log

    # For call_transcription events — transcript is ready, fetch it
    elif state == "call_transcription":
        webhook_event.processed = True
        await db.commit()
        asyncio.create_task(_fetch_and_store_transcript(call_id))
        return None

    # For recording events — update recording URL
    elif state == "recording":
        existing = await db.execute(
            select(CallLog).where(CallLog.call_id == call_id)
        )
        call_log = existing.scalars().first()
        if call_log:
            recording_url = payload.get("recording_url")
            if recording_url:
                call_log.recording_url = recording_url
                call_log.was_recorded = True
                webhook_event.processed = True
                await db.commit()
        return call_log

    # For other states (ringing, connected, missed, etc.) — just log
    else:
        webhook_event.processed = True
        await db.commit()
        logger.info(f"Call {call_id} state: {state}")
        return None


async def _upsert_call_log(db: AsyncSession, payload: dict) -> CallLog:
    """Create or update a CallLog from a webhook hangup event."""
    call_id = payload.get("call_id")

    # Check for existing record
    result = await db.execute(
        select(CallLog).where(CallLog.call_id == call_id)
    )
    call_log = result.scalars().first()

    if not call_log:
        call_log = CallLog(call_id=call_id)
        db.add(call_log)

    # Map webhook payload to CSV-equivalent fields
    call_log.direction = payload.get("direction")
    call_log.external_number = payload.get("external_number")
    call_log.internal_number = payload.get("internal_number")

    # Timestamps
    call_log.date_started = parse_timestamp(payload.get("date_started"))
    call_log.date_connected = parse_timestamp(payload.get("date_connected"))
    call_log.date_ended = parse_timestamp(payload.get("date_ended"))

    # Duration
    call_log.talk_duration = safe_float(payload.get("duration"))

    # Target info (the agent/user/call center handling the call)
    target = payload.get("target", {})
    if target:
        call_log.target_id = target.get("id")
        call_log.target_type = target.get("type")
        call_log.name = target.get("name")

    # Contact info (the external party)
    contact = payload.get("contact", {})
    if contact:
        call_log.external_number = contact.get("phone") or call_log.external_number

    # Recording
    call_log.recording_url = payload.get("recording_url")
    if call_log.recording_url:
        call_log.was_recorded = True

    # Categories/labels
    labels = payload.get("labels", [])
    if labels:
        call_log.categories = ",".join(labels)

    # Determine category from state and direction
    direction = payload.get("direction", "")
    duration = safe_float(payload.get("duration"))
    if direction == "inbound":
        if duration and duration > 0:
            call_log.category = "incoming"
        else:
            call_log.category = "missed"
    elif direction == "outbound":
        if duration and duration > 0:
            call_log.category = "outgoing"
        else:
            call_log.category = "cancelled"

    # Store the raw payload for debugging
    call_log.raw_payload = payload

    await db.flush()
    logger.info(f"Stored call log for {call_id} ({call_log.category})")
    return call_log


async def _fetch_and_store_transcript(call_id: str):
    """
    Background task: fetch transcript from Dialpad API and store it.
    Runs async so the webhook response isn't delayed.
    """
    # Small delay to give Dialpad time to finalize the transcript
    await asyncio.sleep(5)

    from app.database import AsyncSessionLocal

    try:
        transcript_data = await dialpad_client.get_transcript(call_id)

        async with AsyncSessionLocal() as db:
            # Check if transcript record exists
            result = await db.execute(
                select(CallTranscript).where(CallTranscript.call_id == call_id)
            )
            transcript = result.scalars().first()

            if not transcript:
                transcript = CallTranscript(call_id=call_id)
                db.add(transcript)

            if transcript_data:
                moments = transcript_data.get("moments", [])
                transcript.moments = moments
                transcript.summary = transcript_data.get("summary")

                # Build full text from moments
                full_text_parts = []
                for moment in moments:
                    speaker = moment.get("speaker", "Unknown")
                    text = moment.get("text", "")
                    full_text_parts.append(f"{speaker}: {text}")
                transcript.full_text = "\n".join(full_text_parts)

                transcript.fetch_status = "success"
                logger.info(f"Transcript stored for call {call_id} ({len(moments)} moments)")
            else:
                transcript.fetch_status = "not_available"
                logger.info(f"No transcript available for call {call_id}")

            transcript.fetched_at = datetime.utcnow()
            await db.commit()

    except Exception as e:
        logger.error(f"Error fetching/storing transcript for call {call_id}: {e}")
        # Mark as failed
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(CallTranscript).where(CallTranscript.call_id == call_id)
                )
                transcript = result.scalars().first()
                if not transcript:
                    transcript = CallTranscript(call_id=call_id)
                    db.add(transcript)
                transcript.fetch_status = "failed"
                transcript.fetched_at = datetime.utcnow()
                await db.commit()
        except Exception as inner_e:
            logger.error(f"Error marking transcript as failed for {call_id}: {inner_e}")
