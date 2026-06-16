"""
Tests for NotificationService: daily reminders, weekly recaps, and anomaly detection.
"""

import pytest
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

from app.db.models import Base, User, Transaction
from app.db.repositories import UserRepository, TransactionRepository
from app.services.notification_service import NotificationService

# Mock Bot for testing Telegram deliveries
class MockBot:
    def __init__(self):
        self.sent_messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        })


# Mock Datetime subclass to control time in tests
class MockDatetime(datetime):
    _mock_now_utc = None

    @classmethod
    def now(cls, tz=None):
        if cls._mock_now_utc is None:
            return super().now(tz)
        if tz is None:
            return cls._mock_now_utc.replace(tzinfo=None)
        return cls._mock_now_utc.astimezone(tz)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing notifications."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture(autouse=True)
def patch_get_session(db_session):
    """Automatically patch get_session in notification_service to yield test db_session."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_get_session():
        yield db_session

    with patch("app.services.notification_service.get_session", side_effect=mock_get_session):
        yield


@pytest.fixture(autouse=True)
def patch_datetime():
    """Patch datetime inside notification_service with MockDatetime."""
    with patch("app.services.notification_service.datetime", MockDatetime):
        yield


# ── Tests ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_daily_reminders(db_session):
    """Test that daily reminders are sent in the evening and not duplicated."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create(platform_id="tg_123", name="Hafiz")
    user.timezone = "Asia/Jakarta"  # UTC+7
    await db_session.commit()

    bot = MockBot()
    service = NotificationService()

    # Scenario 1: It is 19:30 local time (12:30 UTC) - too early for reminder
    MockDatetime._mock_now_utc = datetime(2026, 6, 15, 12, 30, tzinfo=timezone.utc)
    await service.send_daily_reminders(bot)
    assert len(bot.sent_messages) == 0

    # Scenario 2: It is 20:30 local time (13:30 UTC) - reminder should be sent
    MockDatetime._mock_now_utc = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc)
    await service.send_daily_reminders(bot)
    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0]["chat_id"] == "tg_123"
    assert "pengeluaran yang belum dicatat" in bot.sent_messages[0]["text"]

    # Refresh user
    await db_session.refresh(user)
    assert user.last_reminder_date == date(2026, 6, 15)

    # Scenario 3: Send again at 21:30 local time - should not send duplicate reminder
    MockDatetime._mock_now_utc = datetime(2026, 6, 15, 14, 30, tzinfo=timezone.utc)
    bot.sent_messages.clear()
    await service.send_daily_reminders(bot)
    assert len(bot.sent_messages) == 0


@pytest.mark.asyncio
async def test_send_weekly_summaries(db_session):
    """Test that weekly summaries are sent on Monday mornings with correct recaps."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create(platform_id="tg_123", name="Hafiz")
    user.timezone = "Asia/Jakarta"
    await db_session.flush()

    # Monday, June 15, 2026 at 08:30 WIB (01:30 UTC)
    mock_monday_utc = datetime(2026, 6, 15, 1, 30, tzinfo=timezone.utc)
    MockDatetime._mock_now_utc = mock_monday_utc

    # Add historical transactions for last week (June 8 to June 14)
    txn_repo = TransactionRepository(db_session)
    # Income: 1.500.000
    await txn_repo.create(user.id, amount=1500000, type="income", category="Gaji", transaction_date=date(2026, 6, 10))
    # Expenses: 300.000 on Makanan, 150.000 on Transportasi
    await txn_repo.create(user.id, amount=300000, type="expense", category="Makanan & Minuman", transaction_date=date(2026, 6, 11))
    await txn_repo.create(user.id, amount=150000, type="expense", category="Transportasi", transaction_date=date(2026, 6, 12))
    await db_session.commit()

    bot = MockBot()
    service = NotificationService()

    # Scenario 1: Run on Sunday evening - should not trigger
    MockDatetime._mock_now_utc = mock_monday_utc - timedelta(hours=12) # Sunday evening
    await service.send_weekly_summaries(bot)
    assert len(bot.sent_messages) == 0

    # Scenario 2: Run on Monday morning - should trigger and compile recap
    MockDatetime._mock_now_utc = mock_monday_utc
    await service.send_weekly_summaries(bot)
    assert len(bot.sent_messages) == 1
    
    text = bot.sent_messages[0]["text"]
    assert "Total Pemasukan: *Rp 1.500.000*" in text
    assert "Total Pengeluaran: *Rp 450.000*" in text
    assert "Selisih (Tabungan): *Rp 1.050.000*" in text
    assert "Makanan & Minuman: *Rp 300.000*" in text
    assert "Transportasi: *Rp 150.000*" in text

    # Refresh user
    await db_session.refresh(user)
    assert user.last_weekly_report_date == date(2026, 6, 15)

    # Scenario 3: Run again - should not duplicate weekly summary
    bot.sent_messages.clear()
    await service.send_weekly_summaries(bot)
    assert len(bot.sent_messages) == 0


@pytest.mark.asyncio
async def test_check_spending_anomaly(db_session):
    """Test spending anomaly detection when user weekly spending exceeds 3x their average."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create(platform_id="tg_123", name="Hafiz")
    user.timezone = "Asia/Jakarta"
    # Set user registration date to 15 days before the mock date so we have exactly 2 completed weeks of history
    user.created_at = datetime(2026, 6, 2, 10, 0)
    await db_session.commit()

    txn_repo = TransactionRepository(db_session)
    bot = MockBot()
    service = NotificationService()

    # Set mock current date to Wednesday, June 17, 2026 (WIB)
    mock_now_utc = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    MockDatetime._mock_now_utc = mock_now_utc
    local_today = date(2026, 6, 17)

    # Create completed weeks history (average weekly spending: 100.000)
    # Week 1: 100.000
    await txn_repo.create(user.id, amount=100000, type="expense", transaction_date=date(2026, 6, 1))
    # Week 2: 100.000
    await txn_repo.create(user.id, amount=100000, type="expense", transaction_date=date(2026, 6, 8))
    await db_session.commit()

    # Scenario 1: Current week spending is normal (e.g., 150.000). 150.000 < 3 * 100.000.
    await txn_repo.create(user.id, amount=150000, type="expense", transaction_date=date(2026, 6, 16))
    await db_session.commit()
    
    await service.check_spending_anomaly(user.platform_id, bot)
    assert len(bot.sent_messages) == 0

    # Scenario 2: Current week spending exceeds 3x average (e.g., add 200.000 more expense, total = 350.000 >= 300.000)
    await txn_repo.create(user.id, amount=200000, type="expense", transaction_date=date(2026, 6, 17))
    await db_session.commit()

    await service.check_spending_anomaly(user.platform_id, bot)
    assert len(bot.sent_messages) == 1
    assert "Pengeluaran Tidak Wajar Terdeteksi" in bot.sent_messages[0]["text"]
    assert "3x lebih tinggi" in bot.sent_messages[0]["text"]

    # Refresh user to verify last_anomaly_alert_date is updated
    await db_session.refresh(user)
    assert user.last_anomaly_alert_date == local_today

    # Scenario 3: Log another expense in the same week - should not duplicate the anomaly alert
    bot.sent_messages.clear()
    await txn_repo.create(user.id, amount=50000, type="expense", transaction_date=date(2026, 6, 17))
    await db_session.commit()
    
    await service.check_spending_anomaly(user.platform_id, bot)
    assert len(bot.sent_messages) == 0
