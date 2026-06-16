"""
Notification Service — handles daily reminders, weekly recaps, and anomaly detection.
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select

from app.db.database import get_session
from app.db.repositories import UserRepository, TransactionRepository
from app.db.models import User

logger = logging.getLogger(__name__)


def format_currency(amount: float, currency: str = "IDR") -> str:
    """Format amount as currency."""
    if currency == "IDR":
        return f"Rp {amount:,.0f}".replace(",", ".")
    return f"{amount:,.2f} {currency}"


class NotificationService:
    """Orchestrates automated notifications and spending checks."""

    async def send_daily_reminders(self, bot) -> None:
        """
        Send evening reminder to users to log unrecorded expenses.
        Runs daily around 20:00 (8 PM) user's local time.
        """
        async with get_session() as session:
            # Fetch all users
            stmt = select(User)
            result = await session.execute(stmt)
            users = result.scalars().all()

            now_utc = datetime.now(ZoneInfo("UTC"))

            for user in users:
                try:
                    user_tz = ZoneInfo(user.timezone)
                    local_now = now_utc.astimezone(user_tz)
                    local_today = local_now.date()
                    local_hour = local_now.hour

                    # Check if local time is 8 PM (20:00) or later
                    if local_hour >= 20:
                        # Check if reminder not yet sent today
                        if not user.last_reminder_date or user.last_reminder_date < local_today:
                            logger.info(f"Sending daily reminder to user {user.platform_id} ({user.timezone})")
                            
                            # Update field before sending to avoid double sending in case of network retry
                            user.last_reminder_date = local_today
                            session.add(user)
                            await session.flush()

                            reminder_text = (
                                "👋 *Halo! Hari ini ada pengeluaran yang belum dicatat?*\n\n"
                                "Ketik langsung transaksimu saja ya untuk mencatatnya sekarang! 💰\n"
                                "_Contoh: Kopi 15000_"
                            )

                            await bot.send_message(
                                chat_id=user.platform_id,
                                text=reminder_text,
                                parse_mode="Markdown",
                            )

                except Exception as e:
                    logger.error(f"Error sending daily reminder to user {user.platform_id}: {e}", exc_info=True)

    async def send_weekly_summaries(self, bot) -> None:
        """
        Send weekly recap to users on Monday morning.
        Runs weekly around 08:00 (8 AM) user's local time.
        """
        async with get_session() as session:
            stmt = select(User)
            result = await session.execute(stmt)
            users = result.scalars().all()

            now_utc = datetime.now(ZoneInfo("UTC"))

            for user in users:
                try:
                    user_tz = ZoneInfo(user.timezone)
                    local_now = now_utc.astimezone(user_tz)
                    local_today = local_now.date()
                    local_hour = local_now.hour
                    local_weekday = local_now.weekday()

                    # Monday = 0, check if Monday and >= 8 AM
                    if local_weekday == 0 and local_hour >= 8:
                        if not user.last_weekly_report_date or user.last_weekly_report_date < local_today:
                            logger.info(f"Generating weekly summary for user {user.platform_id} ({user.timezone})")
                            
                            # Mark as sent
                            user.last_weekly_report_date = local_today
                            session.add(user)
                            await session.flush()

                            # Compute start and end dates of the previous completed week (last Monday to Sunday)
                            # local_today is Monday, so local_today - 7 is last Monday
                            prev_week_start = local_today - timedelta(days=7)
                            prev_week_end = prev_week_start + timedelta(days=6)

                            txn_repo = TransactionRepository(session)
                            txns = await txn_repo.get_by_date_range(
                                user_id=user.id,
                                start_date=prev_week_start,
                                end_date=prev_week_end,
                                limit=1000,
                            )

                            # Calculate totals
                            total_income = sum(t.amount for t in txns if t.type == "income")
                            total_expense = sum(t.amount for t in txns if t.type == "expense")
                            net = total_income - total_expense

                            # Calculate top categories
                            category_expenses = {}
                            for t in txns:
                                if t.type == "expense":
                                    cat = t.category or "Lainnya"
                                    category_expenses[cat] = category_expenses.get(cat, 0.0) + t.amount

                            top_categories = sorted(category_expenses.items(), key=lambda x: x[1], reverse=True)[:3]

                            # Format message
                            start_str = prev_week_start.strftime("%d %b")
                            end_str = prev_week_end.strftime("%d %b %Y")

                            recap_text = (
                                f"📊 *Ringkasan Mingguan Jarfin*\n"
                                f"📅 Periode: *{start_str} - {end_str}*\n\n"
                                f"💰 Total Pemasukan: *{format_currency(total_income, user.currency)}*\n"
                                f"💸 Total Pengeluaran: *{format_currency(total_expense, user.currency)}*\n"
                                f"⚖️ Selisih (Tabungan): *{format_currency(net, user.currency)}*\n\n"
                            )

                            if top_categories:
                                recap_text += "📂 *Top Pengeluaran Kategori:*\n"
                                for i, (cat, amt) in enumerate(top_categories, 1):
                                    recap_text += f"{i}. {cat}: *{format_currency(amt, user.currency)}*\n"
                            else:
                                recap_text += "📂 Tidak ada pengeluaran tercatat minggu lalu."

                            recap_text += "\nTetap pantau keuanganmu minggu ini ya! 🚀"

                            await bot.send_message(
                                chat_id=user.platform_id,
                                text=recap_text,
                                parse_mode="Markdown",
                            )

                except Exception as e:
                    logger.error(f"Error sending weekly report to user {user.platform_id}: {e}", exc_info=True)

    async def check_spending_anomaly(self, platform_id: str, bot) -> None:
        """
        Check if user's current week spending is >= 3x the average of previous completed weeks.
        If yes, trigger a warning alert.
        """
        async with get_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_platform_id(platform_id)
            if user is None:
                return

            try:
                user_tz = ZoneInfo(user.timezone)
                local_now = datetime.now(ZoneInfo("UTC")).astimezone(user_tz)
                local_today = local_now.date()

                # Get start of the current week (Monday)
                curr_week_start = local_today - timedelta(days=local_today.weekday())

                # Check if we already sent an anomaly warning this week
                if user.last_anomaly_alert_date and user.last_anomaly_alert_date >= curr_week_start:
                    return

                # Calculate completed weeks since user's registration
                days_since_created = (local_today - user.created_at.date()).days
                weeks_since_created = days_since_created // 7

                # We check up to last 4 completed weeks
                num_weeks_to_check = min(4, weeks_since_created)
                if num_weeks_to_check < 1:
                    return  # No completed weeks history to compare against

                txn_repo = TransactionRepository(session)

                # Get spending totals for each completed week
                completed_weeks_totals = []
                for i in range(1, num_weeks_to_check + 1):
                    w_start = curr_week_start - timedelta(days=7 * i)
                    w_end = w_start + timedelta(days=6)

                    w_txns = await txn_repo.get_by_date_range(
                        user_id=user.id,
                        start_date=w_start,
                        end_date=w_end,
                        limit=1000,
                    )
                    w_total = sum(t.amount for t in w_txns if t.type == "expense")
                    completed_weeks_totals.append(w_total)

                avg_weekly_spending = sum(completed_weeks_totals) / len(completed_weeks_totals)

                # If average weekly spending is non-zero, check current week spending
                if avg_weekly_spending > 0:
                    # Current week spending (from Monday to today)
                    curr_txns = await txn_repo.get_by_date_range(
                        user_id=user.id,
                        start_date=curr_week_start,
                        end_date=local_today,
                        limit=1000,
                    )
                    curr_total = sum(t.amount for t in curr_txns if t.type == "expense")

                    # If current spending is at least 3x the average, alert user
                    if curr_total >= 3.0 * avg_weekly_spending:
                        # factor = curr_total / avg_weekly_spending
                        # E.g. 3x avg means 2x higher than usual (200% higher)
                        multiplier_higher = int(round(curr_total / avg_weekly_spending)) - 1
                        if multiplier_higher >= 2:
                            # Update field to prevent duplicate warnings this week
                            user.last_anomaly_alert_date = local_today
                            session.add(user)
                            await session.flush()

                            alert_text = (
                                f"⚠️ *Pengeluaran Tidak Wajar Terdeteksi!*\n\n"
                                f"Pengeluaran lo minggu ini *{multiplier_higher}x lebih tinggi* dari biasanya "
                                f"(mencapai *{format_currency(curr_total, user.currency)}*). Mau cek? Ketik /riwayat"
                            )

                            logger.info(f"Sending anomaly spending alert to user {user.platform_id} (factor: {curr_total / avg_weekly_spending:.2f})")
                            await bot.send_message(
                                chat_id=user.platform_id,
                                text=alert_text,
                                parse_mode="Markdown",
                            )

            except Exception as e:
                logger.error(f"Error checking spending anomaly for user {platform_id}: {e}", exc_info=True)
