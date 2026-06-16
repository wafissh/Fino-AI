"""
Database engine, session management, and initialization.
Supports SQLite (dev) and PostgreSQL (prod) via DATABASE_URL.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from app.config import get_settings

# ── Engine & Session Factory ────────────────────────────────────────────────

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # SQLite-specific: allow async usage
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Session Context Manager ────────────────────────────────────────────────

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Database Initialization ────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables if they don't exist."""
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Check if type column exists in transactions table, if not add it
        def add_column_if_not_exists(connection):
            cursor = connection.connection.cursor()
            try:
                # Try to select the type column
                cursor.execute("SELECT type FROM transactions LIMIT 1")
            except Exception:
                # Column doesn't exist, alter table
                try:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN type VARCHAR NOT NULL DEFAULT 'expense'")
                except Exception as ex:
                    print(f"Error altering table transactions: {ex}")

            # Check if notification tracking columns exist in users table, if not add them
            for col in ["last_reminder_date", "last_weekly_report_date", "last_anomaly_alert_date"]:
                try:
                    cursor.execute(f"SELECT {col} FROM users LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(f"ALTER TABLE users ADD COLUMN {col} DATE")
                    except Exception as ex:
                        print(f"Error altering table users to add {col}: {ex}")

        await conn.run_sync(add_column_if_not_exists)


async def close_db() -> None:
    """Dispose the engine and close all connections."""
    await engine.dispose()
