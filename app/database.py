import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    from app.views import ALL_VIEWS

    async with engine.begin() as conn:
        # Create tables
        await conn.run_sync(Base.metadata.create_all)

        # Drop and recreate views (CREATE OR REPLACE can't rename columns)
        for view_name in ["v_call_logs", "v_sms_events", "v_transcripts", "v_recordings"]:
            await conn.execute(text(f"DROP VIEW IF EXISTS {view_name} CASCADE"))
        for view_sql in ALL_VIEWS:
            await conn.execute(text(view_sql))

        logger.info("Database tables and views initialized")
