"""
PostgreSQL views that extract structured data from raw_events JSONB payloads.
All timestamps are converted from UTC to America/Los_Angeles to match the Dialpad CSV export.

- v_call_logs:    matches the Dialpad CSV call log export format exactly
- v_sms_events:   structured SMS event data
- v_transcripts:  joins call_transcripts with call context from raw_events
- v_recordings:   recording URLs and details
"""

# Dialpad sends timestamps as milliseconds (epoch ms), so we divide by 1000.
# Then convert to America/Los_Angeles to match the CSV export format.
# Helper: ts_pacific(field) = to_timestamp(field::bigint / 1000.0) AT TIME ZONE 'America/Los_Angeles'

VIEW_CALL_LOGS = """
CREATE OR REPLACE VIEW v_call_logs AS
SELECT
    -- Timestamps (epoch ms → America/Los_Angeles to match CSV)
    to_timestamp((payload->>'date_started')::bigint / 1000.0)
        AT TIME ZONE 'America/Los_Angeles'                                           AS date_started,
    payload->>'call_id'                                                     AS call_id,

    -- Category: derive from direction + duration like CSV does
    CASE
        WHEN payload->>'direction' = 'inbound' AND (payload->>'duration')::float > 0
            THEN 'incoming'
        WHEN payload->>'direction' = 'inbound' AND (payload->>'duration')::float = 0
             AND payload->'labels' @> '["voicemail"]'::jsonb
            THEN 'voicemail'
        WHEN payload->>'direction' = 'inbound' AND (payload->>'duration')::float = 0
             AND (payload->'target'->>'type' = 'call_center')
            THEN 'abandoned'
        WHEN payload->>'direction' = 'inbound' AND (payload->>'duration')::float = 0
            THEN 'missed'
        WHEN payload->>'direction' = 'outbound' AND (payload->>'duration')::float > 0
            THEN 'outgoing'
        WHEN payload->>'direction' = 'outbound' AND (payload->>'duration')::float = 0
            THEN 'cancelled'
        ELSE 'unknown'
    END                                                                     AS category,

    payload->>'direction'                                                   AS direction,
    payload->>'external_number'                                             AS external_number,
    payload->>'internal_number'                                             AS internal_number,

    -- More timestamps (all converted to America/Los_Angeles)
    CASE WHEN payload->>'date_first_rang' IS NOT NULL AND payload->>'date_first_rang' != 'null'
        THEN to_timestamp((payload->>'date_first_rang')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS date_first_rang,
    CASE WHEN payload->>'date_queued' IS NOT NULL AND payload->>'date_queued' != 'null'
        THEN to_timestamp((payload->>'date_queued')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS date_queued,
    CASE WHEN payload->>'date_rang' IS NOT NULL AND payload->>'date_rang' != 'null'
        THEN to_timestamp((payload->>'date_rang')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS date_rang,
    CASE WHEN payload->>'date_connected' IS NOT NULL AND payload->>'date_connected' != 'null'
        THEN to_timestamp((payload->>'date_connected')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS date_connected,
    CASE WHEN payload->>'date_ended' IS NOT NULL AND payload->>'date_ended' != 'null'
        THEN to_timestamp((payload->>'date_ended')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS date_ended,

    -- Target (agent / call center)
    payload->'target'->>'id'                                                AS target_id,
    CASE
        WHEN payload->'target'->>'type' = 'user' THEN 'UserProfile'
        WHEN payload->'target'->>'type' = 'call_center' THEN 'CallCenter'
        ELSE payload->'target'->>'type'
    END                                                                     AS target_kind,
    payload->'target'->>'type'                                              AS target_type,
    payload->'target'->>'name'                                              AS name,
    payload->'target'->>'email'                                             AS email,

    -- Recording
    (payload->>'was_recorded')::boolean                                     AS was_recorded,

    -- Entry point
    payload->>'entry_point_call_id'                                         AS entry_point_call_id,
    payload->'entry_point_target'->>'id'                                    AS entry_point_target_id,
    CASE
        WHEN payload->'entry_point_target'->>'type' = 'user' THEN 'UserProfile'
        WHEN payload->'entry_point_target'->>'type' = 'call_center' THEN 'CallCenter'
        ELSE payload->'entry_point_target'->>'type'
    END                                                                     AS entry_point_target_kind,
    payload->'proxy_target'->>'id'                                          AS proxy_target_id,

    -- Voicemail
    CASE WHEN payload->>'voicemail_link' IS NOT NULL AND payload->>'voicemail_link' != 'null'
        THEN true ELSE false END                                            AS voicemail,

    -- Transfer
    CASE WHEN (payload->>'is_transferred')::boolean THEN 'transferred'
        ELSE NULL END                                                       AS transferred_to,
    NULL::text                                                              AS transferred_to_contact_id,
    NULL::text                                                              AS transferred_from_target_id,

    -- Organization
    payload->'target'->>'office_id'                                         AS office_id,
    NULL::text                                                              AS company_id,

    -- Device & context
    NULL::text                                                              AS device,
    payload->'integrations'->>'salesforce_activity_id'                      AS salesforce_activity_id,
    'America/Los_Angeles'::text                                                      AS timezone,
    payload->>'target_availability_status'                                  AS availability,

    -- Durations
    (payload->>'total_duration')::float / 1000.0                            AS time_in_system,
    NULL::text                                                              AS callback_type,
    NULL::text                                                              AS callback_id,
    payload->>'master_call_id'                                              AS master_call_id,

    -- Time to answer = date_connected - date_started (in seconds)
    CASE WHEN payload->>'date_connected' IS NOT NULL AND payload->>'date_connected' != 'null'
        THEN ((payload->>'date_connected')::bigint - (payload->>'date_started')::bigint) / 1000.0
        ELSE NULL END                                                       AS time_to_answer,

    -- Callback timestamps (converted to America/Los_Angeles)
    NULL::timestamp                                                         AS date_callback_connected,
    NULL::timestamp                                                         AS date_callback_ended,
    NULL::timestamp                                                         AS date_anonymized,

    -- Categories (from labels array -> comma-separated string)
    (SELECT string_agg(elem::text, ',')
     FROM jsonb_array_elements_text(payload->'labels') AS elem)             AS categories,

    -- Ringing duration = date_connected - date_rang (in seconds)
    CASE WHEN payload->>'date_rang' IS NOT NULL AND payload->>'date_rang' != 'null'
              AND payload->>'date_connected' IS NOT NULL AND payload->>'date_connected' != 'null'
        THEN ((payload->>'date_connected')::bigint - (payload->>'date_rang')::bigint) / 1000.0
        ELSE 0 END                                                          AS ringing_duration,
    0                                                                       AS ringing_occurrences,

    -- Hold
    (payload->>'hold_time')::float / 1000.0                                 AS hold_duration,
    0                                                                       AS hold_occurrences,

    -- Talk duration (Dialpad sends talk_time in ms)
    (payload->>'talk_time')::float / 1000.0                                 AS talk_duration,

    -- Queue
    NULL::float                                                             AS queued_duration,
    0                                                                       AS queued_occurrences,

    -- Wrapup
    NULL::float                                                             AS wrapup_duration,

    -- Participant
    NULL::text                                                              AS participant_type,

    -- AI metrics (not in webhook payload — only in stats export)
    NULL::float                                                             AS percent_ai_talk_time,
    NULL::float                                                             AS percent_ai_listen_time,
    NULL::float                                                             AS percent_ai_silent_time,

    -- Campaign
    NULL::text                                                              AS campaign_id,

    -- Internal call flag
    false                                                                   AS is_internal,

    -- Extra fields from webhook (not in CSV but useful)
    payload->'contact'->>'id'                                               AS contact_id,
    payload->'contact'->>'name'                                             AS contact_name,
    payload->'contact'->>'phone'                                            AS contact_phone,
    payload->'contact'->>'type'                                             AS contact_type,
    (payload->>'mos_score')::float                                          AS mos_score,
    payload->>'public_call_review_share_link'                               AS public_call_review_link,
    payload->>'company_call_review_share_link'                              AS company_call_review_link,

    -- Raw event metadata
    re.id                                                                   AS raw_event_id,
    re.received_at AT TIME ZONE 'America/Los_Angeles'                                AS webhook_received_at

FROM raw_events re
WHERE re.event_type = 'call'
  AND re.event_subtype = 'hangup';
"""


VIEW_SMS_EVENTS = """
CREATE OR REPLACE VIEW v_sms_events AS
SELECT
    re.id                                                                   AS raw_event_id,
    re.received_at AT TIME ZONE 'America/Los_Angeles'                                AS webhook_received_at,

    payload->>'direction'                                                   AS direction,
    payload->>'from_number'                                                 AS from_number,
    payload->>'to_number'                                                   AS to_number,
    payload->>'text'                                                        AS message_text,
    payload->>'mms_url'                                                     AS mms_url,

    -- Contact
    payload->'contact'->>'id'                                               AS contact_id,
    payload->'contact'->>'name'                                             AS contact_name,
    payload->'contact'->>'phone'                                            AS contact_phone,

    -- Target
    payload->'target'->>'id'                                                AS target_id,
    payload->'target'->>'name'                                              AS target_name,
    payload->'target'->>'type'                                              AS target_type,

    CASE WHEN payload->>'event_timestamp' IS NOT NULL
        THEN to_timestamp((payload->>'event_timestamp')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE re.received_at AT TIME ZONE 'America/Los_Angeles'
    END                                                                     AS event_timestamp

FROM raw_events re
WHERE re.event_type = 'sms';
"""


VIEW_TRANSCRIPTS = """
CREATE OR REPLACE VIEW v_transcripts AS
SELECT
    ct.call_id,
    ct.summary,
    ct.full_text,
    ct.moments                                                              AS lines,
    ct.fetch_status,
    ct.fetched_at AT TIME ZONE 'America/Los_Angeles'                        AS fetched_at,

    -- Count of actual spoken transcript lines vs AI moment tags
    (SELECT count(*) FROM json_array_elements(ct.moments) elem
     WHERE elem->>'type' = 'transcript')                                    AS transcript_line_count,
    (SELECT count(*) FROM json_array_elements(ct.moments) elem
     WHERE elem->>'type' = 'moment')                                        AS ai_moment_count,

    -- Call context from the hangup event
    hangup.payload->>'direction'                                            AS direction,
    hangup.payload->>'external_number'                                      AS external_number,
    hangup.payload->>'internal_number'                                      AS internal_number,
    hangup.payload->'target'->>'name'                                       AS agent_name,
    hangup.payload->'target'->>'email'                                      AS agent_email,
    hangup.payload->'contact'->>'name'                                      AS contact_name,
    hangup.payload->'contact'->>'phone'                                     AS contact_phone,
    CASE WHEN hangup.payload->>'date_started' IS NOT NULL
        THEN to_timestamp((hangup.payload->>'date_started')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS call_date,
    (hangup.payload->>'talk_time')::float / 1000.0                          AS talk_duration_sec,
    hangup.payload->>'public_call_review_share_link'                        AS call_review_link

FROM call_transcripts ct
LEFT JOIN raw_events hangup
    ON hangup.event_type = 'call'
   AND hangup.event_subtype = 'hangup'
   AND hangup.payload->>'call_id' = ct.call_id;
"""


VIEW_RECORDINGS = """
CREATE OR REPLACE VIEW v_recordings AS
SELECT
    payload->>'call_id'                                                     AS call_id,
    re.received_at AT TIME ZONE 'America/Los_Angeles'                                AS webhook_received_at,

    payload->>'direction'                                                   AS direction,
    payload->>'external_number'                                             AS external_number,
    payload->>'internal_number'                                             AS internal_number,
    payload->'target'->>'name'                                              AS target_name,
    payload->'target'->>'type'                                              AS target_type,
    payload->'contact'->>'name'                                             AS contact_name,
    payload->'contact'->>'phone'                                            AS contact_phone,

    -- Recording URLs (Dialpad sends as array)
    payload->'recording_url'                                                AS recording_urls,
    payload->'recording_details'                                            AS recording_details,
    payload->'call_recording_share_links'                                   AS share_links,

    (payload->>'talk_time')::float / 1000.0                                 AS talk_duration_sec,
    CASE WHEN payload->>'date_started' IS NOT NULL
        THEN to_timestamp((payload->>'date_started')::bigint / 1000.0)
            AT TIME ZONE 'America/Los_Angeles'
        ELSE NULL END                                                       AS call_date

FROM raw_events re
WHERE re.event_type = 'call'
  AND re.event_subtype = 'recording';
"""


ALL_VIEWS = [VIEW_CALL_LOGS, VIEW_SMS_EVENTS, VIEW_TRANSCRIPTS, VIEW_RECORDINGS]
