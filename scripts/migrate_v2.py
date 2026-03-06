#!/usr/bin/env python3
"""
Migration script: Event-first redesign (v2).

- Drops FK constraint from call_transcripts
- Drops old webhook_events table
- Creates raw_events table with JSONB + expression index
- Idempotent (safe to run multiple times)

Usage:
    python scripts/migrate_v2.py
"""
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def get_sync_db_url():
    """Convert async DB URL to sync for psycopg2."""
    url = os.getenv("DATABASE_URL", "")
    # Strip async driver prefixes
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


def run_migration():
    db_url = get_sync_db_url()
    if not db_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    print(f"Connecting to: {db_url.split('@')[1] if '@' in db_url else '(local)'}")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    # Step 1: Drop FK from call_transcripts
    print("\n1. Dropping FK constraint from call_transcripts...")
    cur.execute("""
        DO $$
        DECLARE fk_name TEXT;
        BEGIN
            SELECT constraint_name INTO fk_name
            FROM information_schema.table_constraints
            WHERE table_name = 'call_transcripts'
              AND constraint_type = 'FOREIGN KEY';
            IF fk_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE call_transcripts DROP CONSTRAINT ' || fk_name;
                RAISE NOTICE 'Dropped FK: %', fk_name;
            ELSE
                RAISE NOTICE 'No FK found on call_transcripts (already removed)';
            END IF;
        END $$;
    """)
    print("   Done.")

    # Step 2: Drop old webhook_events table
    print("\n2. Dropping old webhook_events table...")
    cur.execute("DROP TABLE IF EXISTS webhook_events;")
    print("   Done.")

    # Step 3: Create raw_events table
    print("\n3. Creating raw_events table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raw_events (
            id SERIAL PRIMARY KEY,
            event_type VARCHAR(64) NOT NULL,
            event_subtype VARCHAR(64),
            received_at TIMESTAMP DEFAULT NOW(),
            payload JSONB NOT NULL
        );
    """)
    print("   Done.")

    # Step 4: Create indexes
    print("\n4. Creating indexes...")
    indexes = [
        ("ix_raw_events_event_type", "CREATE INDEX IF NOT EXISTS ix_raw_events_event_type ON raw_events(event_type);"),
        ("ix_raw_events_received_at", "CREATE INDEX IF NOT EXISTS ix_raw_events_received_at ON raw_events(received_at);"),
        ("ix_raw_events_type_received", "CREATE INDEX IF NOT EXISTS ix_raw_events_type_received ON raw_events(event_type, received_at);"),
        ("ix_raw_events_call_id", "CREATE INDEX IF NOT EXISTS ix_raw_events_call_id ON raw_events((payload->>'call_id'));"),
    ]
    for name, sql in indexes:
        cur.execute(sql)
        print(f"   {name} ✓")

    # Step 5: Verify
    print("\n5. Verifying...")
    cur.execute("SELECT COUNT(*) FROM raw_events;")
    count = cur.fetchone()[0]
    print(f"   raw_events rows: {count}")

    cur.execute("""
        SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE table_name = 'call_transcripts' AND constraint_type = 'FOREIGN KEY';
    """)
    fk_count = cur.fetchone()[0]
    print(f"   call_transcripts FK constraints: {fk_count}")

    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables WHERE table_name = 'webhook_events'
        );
    """)
    old_exists = cur.fetchone()[0]
    print(f"   webhook_events table exists: {old_exists}")

    cur.close()
    conn.close()

    print(f"\n{'='*60}")
    print("Migration complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_migration()
