"""
Jarfin — Telegram Bot Pencatat Keuangan

FastAPI application with Telegram bot running in polling mode (dev)
or webhook mode (production).
"""

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from app.config import get_settings
from app.db.database import init_db, close_db
from app.bot.handlers import (
    start_command,
    help_command,
    ringkasan_command,
    riwayat_command,
    hapus_command,
    settings_command,
    budget_command,
    rutin_command,
    konsul_command,
    handle_text_message,
    handle_photo_message,
    handle_callback_query,
)

# ── Logging setup ───────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Settings ────────────────────────────────────────────────────────────────

settings = get_settings()

# ── Telegram Bot Application ───────────────────────────────────────────────

telegram_app: Application | None = None


def create_telegram_app() -> Application:
    """Create and configure the Telegram bot application."""
    from telegram.request import HTTPXRequest
    
    # Increase timeouts to handle slow/unstable network connections
    request = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
    
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .build()
    )

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ringkasan", ringkasan_command))
    app.add_handler(CommandHandler("riwayat", riwayat_command))
    app.add_handler(CommandHandler("hapus", hapus_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("budget", budget_command))
    app.add_handler(CommandHandler("rutin", rutin_command))
    app.add_handler(CommandHandler("konsul", konsul_command))

    # Callback query handler (inline keyboard buttons)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Message handlers (order matters — commands are handled first by default)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )

    return app


# ── Background Job: Run Due Recurring Transactions ──────────────────────────

async def _recurring_job_loop():
    """
    Background loop that checks and executes due recurring transactions
    every hour. In production, you'd use a proper scheduler (e.g., APScheduler).
    """
    from app.services.recurring_service import RecurringService
    recurring_service = RecurringService()

    while True:
        try:
            executed = await recurring_service.run_due_transactions()
            if executed > 0:
                logger.info(f"✅ Recurring job: executed {executed} transaction(s)")
        except Exception as e:
            logger.error(f"❌ Recurring job error: {e}", exc_info=True)

        # Check every hour
        await asyncio.sleep(3600)


# ── Background Job: Send Daily Reminders and Weekly Summaries ────────────────

async def _notification_job_loop():
    """
    Background loop that checks and sends daily reminders and weekly summaries
    every hour.
    """
    from app.services.notification_service import NotificationService
    notification_service = NotificationService()

    while True:
        try:
            if telegram_app and telegram_app.bot:
                logger.info("Checking daily reminders and weekly summaries...")
                await notification_service.send_daily_reminders(telegram_app.bot)
                await notification_service.send_weekly_summaries(telegram_app.bot)
        except Exception as e:
            logger.error(f"❌ Notification job error: {e}", exc_info=True)

        # Check every hour
        await asyncio.sleep(3600)


# ── FastAPI Lifespan ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global telegram_app

    # Startup
    logger.info("🚀 Starting Jarfin...")

    # Initialize database (creates tables if not exists)
    await init_db()
    logger.info("✅ Database initialized")

    # Create Telegram bot
    telegram_app = create_telegram_app()

    # Start recurring job in background
    recurring_task = asyncio.create_task(_recurring_job_loop())
    logger.info("🔄 Recurring transaction job started")

    # Start notification job in background
    notification_task = asyncio.create_task(_notification_job_loop())
    logger.info("🔄 Notification job started")

    bot_started_successfully = False
    try:
        if settings.bot_mode == "polling":
            # Polling mode for development
            logger.info("🔄 Starting bot in POLLING mode...")
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling(drop_pending_updates=True)
            logger.info("✅ Bot is running in polling mode")
        else:
            # Webhook mode for production
            webhook_url = f"{settings.webhook_url}/webhook"
            logger.info(f"🌐 Setting webhook to: {webhook_url}")
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.bot.set_webhook(url=webhook_url)
            logger.info("✅ Webhook set")
        bot_started_successfully = True
    except Exception as e:
        logger.error(f"❌ Failed to start Telegram bot (possibly due to connection timeout): {e}", exc_info=True)
        logger.warning("⚠️ FastAPI will run, but the Telegram bot will NOT be active.")

    yield

    # Shutdown
    logger.info("🛑 Shutting down Jarfin...")
    recurring_task.cancel()
    notification_task.cancel()
    try:
        await asyncio.gather(recurring_task, notification_task, return_exceptions=True)
    except Exception:
        pass

    if telegram_app and bot_started_successfully:
        try:
            if settings.bot_mode == "polling" and telegram_app.updater:
                await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as e:
            logger.error(f"❌ Error during Telegram bot shutdown: {e}", exc_info=True)
    await close_db()
    logger.info("👋 Goodbye!")


# ── FastAPI App ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Jarfin",
    description="Telegram Bot Pencatat Keuangan",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "bot": "Jarfin", "mode": settings.bot_mode, "version": "0.2.0"}


@app.post("/webhook")
async def webhook(request: Request):
    """Receive Telegram updates via webhook (production mode)."""
    if telegram_app is None:
        return JSONResponse(status_code=503, content={"error": "Bot not initialized"})

    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


# ── CLI Entry Point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
