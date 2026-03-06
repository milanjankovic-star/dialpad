"""
Database models for Dialpad webhook service.
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    Text, Index, JSON
)
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class RawEvent(Base):
    """
    Universal raw event store for all webhook deliveries.
    Captures calls, SMS, and any future event types as JSONB.
    """
    __tablename__ = "raw_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True)     # call, sms, voicemail, etc.
    event_subtype = Column(String(64), nullable=True)               # hangup, recording, inbound, etc.
    received_at = Column(DateTime, default=datetime.utcnow, index=True)
    payload = Column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_raw_events_type_received", "event_type", "received_at"),
    )

    def __repr__(self):
        return f"<RawEvent id={self.id} type={self.event_type}/{self.event_subtype}>"


class CallLog(Base):
    """
    Mirrors the Dialpad call log CSV columns.
    Populated from webhook call events (hangup state).
    """
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core call identifiers
    call_id = Column(String(64), unique=True, nullable=False, index=True)
    master_call_id = Column(String(64), nullable=True)
    entry_point_call_id = Column(String(64), nullable=True)

    # Call classification
    category = Column(String(32), nullable=True)
    direction = Column(String(16), nullable=True)
    is_internal = Column(Boolean, default=False)

    # Phone numbers
    external_number = Column(String(32), nullable=True, index=True)
    internal_number = Column(String(32), nullable=True)

    # Timestamps
    date_started = Column(DateTime, nullable=True, index=True)
    date_first_rang = Column(DateTime, nullable=True)
    date_queued = Column(DateTime, nullable=True)
    date_rang = Column(DateTime, nullable=True)
    date_connected = Column(DateTime, nullable=True)
    date_ended = Column(DateTime, nullable=True)
    date_callback_connected = Column(DateTime, nullable=True)
    date_callback_ended = Column(DateTime, nullable=True)
    date_anonymized = Column(DateTime, nullable=True)

    # Target (agent/user/call center)
    target_id = Column(String(64), nullable=True, index=True)
    target_kind = Column(String(32), nullable=True)
    target_type = Column(String(32), nullable=True)
    name = Column(String(256), nullable=True)
    email = Column(String(256), nullable=True, index=True)

    # Entry point
    entry_point_target_id = Column(String(64), nullable=True)
    entry_point_target_kind = Column(String(32), nullable=True)
    proxy_target_id = Column(String(64), nullable=True)

    # Recording & voicemail
    was_recorded = Column(Boolean, default=False)
    voicemail = Column(Boolean, default=False)
    recording_url = Column(Text, nullable=True)

    # Transfer
    transferred_to = Column(String(256), nullable=True)
    transferred_to_contact_id = Column(String(64), nullable=True)
    transferred_from_target_id = Column(String(64), nullable=True)

    # Organization
    office_id = Column(String(64), nullable=True)
    company_id = Column(String(64), nullable=True)

    # Device & context
    device = Column(String(32), nullable=True)
    timezone = Column(String(64), nullable=True)
    availability = Column(String(16), nullable=True)
    salesforce_activity_id = Column(String(128), nullable=True)

    # Durations & metrics
    time_in_system = Column(Float, nullable=True)
    time_to_answer = Column(Float, nullable=True)
    ringing_duration = Column(Float, nullable=True)
    ringing_occurrences = Column(Integer, nullable=True)
    hold_duration = Column(Float, nullable=True)
    hold_occurrences = Column(Integer, nullable=True)
    talk_duration = Column(Float, nullable=True)
    queued_duration = Column(Float, nullable=True)
    queued_occurrences = Column(Integer, nullable=True)
    wrapup_duration = Column(Float, nullable=True)

    # Participant
    participant_type = Column(String(32), nullable=True)

    # AI metrics
    percent_ai_talk_time = Column(Float, nullable=True)
    percent_ai_listen_time = Column(Float, nullable=True)
    percent_ai_silent_time = Column(Float, nullable=True)

    # Callback
    callback_type = Column(String(32), nullable=True)
    callback_id = Column(String(64), nullable=True)

    # Campaign
    campaign_id = Column(String(64), nullable=True)

    # Categories (comma-separated tags)
    categories = Column(Text, nullable=True)

    # Metadata
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_call_logs_date_direction", "date_started", "direction"),
        Index("ix_call_logs_target_date", "target_id", "date_started"),
    )

    def __repr__(self):
        return f"<CallLog call_id={self.call_id} category={self.category}>"


class CallTranscript(Base):
    """
    Stores AI transcripts fetched from GET /transcripts/{call_id}.
    No FK to call_logs — transcription events can arrive before hangup.
    """
    __tablename__ = "call_transcripts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String(64), unique=True, nullable=False, index=True)

    # Transcript content
    summary = Column(Text, nullable=True)
    moments = Column(JSON, nullable=True)
    full_text = Column(Text, nullable=True)

    # Metadata
    fetched_at = Column(DateTime, default=datetime.utcnow)
    fetch_status = Column(String(16), default="pending")

    def __repr__(self):
        return f"<CallTranscript call_id={self.call_id} status={self.fetch_status}>"
