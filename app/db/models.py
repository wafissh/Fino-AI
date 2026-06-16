"""
SQLAlchemy ORM models matching the CATAT.AI PRD v1.0 schema.

Tables:
  - users: Multi-platform user accounts (Telegram / WhatsApp)
  - transactions: Financial transactions
  - budgets: Monthly budget limits per category
"""

from datetime import datetime, date
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    String,
    Text,
    Float,
    Integer,
    Date,
    DateTime,
    ForeignKey,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ── Base ────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Users ───────────────────────────────────────────────────────────────────

class User(Base):
    """
    Multi-platform user account.
    platform_id = Telegram user ID or WhatsApp phone number.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False, default="telegram")
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="IDR")
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="Asia/Jakarta")
    plan: Mapped[str] = mapped_column(String, nullable=False, default="free")
    last_reminder_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_weekly_report_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_anomaly_alert_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # Relationships
    transactions: Mapped[List["Transaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    budgets: Mapped[List["Budget"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    recurring_transactions: Mapped[List["RecurringTransaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, platform_id='{self.platform_id}', name='{self.name}')>"


# ── Transactions ────────────────────────────────────────────────────────────

class Transaction(Base):
    """
    Financial transaction record.
    source: 'text' (from chat message), 'image' (from receipt photo), 'manual'
    """

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="text")
    type: Mapped[str] = mapped_column(String, nullable=False, default="expense")
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="transactions")

    def __repr__(self) -> str:
        return (
            f"<Transaction(id={self.id}, amount={self.amount}, "
            f"category='{self.category}', date={self.date})>"
        )


# ── Budgets ─────────────────────────────────────────────────────────────────

class Budget(Base):
    """
    Monthly budget limit per category.
    month format: 'YYYY-MM' (e.g., '2026-06')
    """

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    month: Mapped[str] = mapped_column(String, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="budgets")

    def __repr__(self) -> str:
        return (
            f"<Budget(id={self.id}, category='{self.category}', "
            f"amount={self.amount}, month='{self.month}')>"
        )


# ── Recurring Transactions ───────────────────────────────────────────────────

class RecurringTransaction(Base):
    """
    Recurring (scheduled) transaction.

    frequency: 'daily', 'weekly', 'monthly'
    day_of_month: for monthly (1-28), day of week for weekly (0=Mon, 6=Sun)
    next_run_date: next date the transaction should be auto-executed
    is_active: False if user has paused/deleted the recurring
    """

    __tablename__ = "recurring_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False, default="expense")
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    frequency: Mapped[str] = mapped_column(String, nullable=False, default="monthly")
    day_of_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    next_run_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Integer, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="recurring_transactions")

    def __repr__(self) -> str:
        return (
            f"<RecurringTransaction(id={self.id}, amount={self.amount}, "
            f"category='{self.category}', frequency='{self.frequency}')>"
        )

