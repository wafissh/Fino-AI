"""
Telegram bot command and message handlers.

Supported:
  Commands: /start, /help, /ringkasan, /riwayat, /settings, /budget, /rutin, /hapus
  Messages: text (transaction input), photo (receipt/nota)
  Callbacks: inline keyboard button presses
"""

import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes

from app.db.database import get_session
from app.db.repositories import UserRepository, TransactionRepository
from app.services.transaction_service import TransactionService
from app.services.budget_service import BudgetService
from app.services.ocr_service import OCRService
from app.services.recurring_service import RecurringService
from app.bot.keyboards import (
    build_main_menu_keyboard,
    build_confirm_keyboard,
    build_category_keyboard,
    build_settings_keyboard,
    build_currency_keyboard,
    build_timezone_keyboard,
    build_budget_menu_keyboard,
    build_budget_category_keyboard,
    build_budget_delete_keyboard,
    build_budget_delete_confirm_keyboard,
    build_history_period_keyboard,
    build_history_nav_keyboard,
    build_delete_confirm_keyboard,
    build_recurring_menu_keyboard,
    build_recurring_category_keyboard,
    build_recurring_frequency_keyboard,
    build_recurring_item_keyboard,
)
from app.config import get_settings
from app.ai.parser import TransactionParser

logger = logging.getLogger(__name__)

# ── Service initialization ──────────────────────────────────────────────────
# AI provider is injected here — swap for another provider by changing AI_PROVIDER env.

settings = get_settings()
if settings.ai_provider == "groq":
    from app.ai.groq_provider import GroqProvider
    _ai_provider = GroqProvider()
elif settings.ai_provider == "qwen":
    from app.ai.qwen_provider import QwenProvider
    _ai_provider = QwenProvider()
else:
    from app.ai.gemini_provider import GeminiProvider
    _ai_provider = GeminiProvider()

_parser = TransactionParser(provider=_ai_provider)
_transaction_service = TransactionService(parser=_parser)
_budget_service = BudgetService()
_ocr_service = OCRService()
_recurring_service = RecurringService()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _format_currency(amount: float, currency: str = "IDR") -> str:
    """Format amount as Indonesian Rupiah."""
    if currency == "IDR":
        return f"Rp {amount:,.0f}".replace(",", ".")
    return f"{amount:,.2f} {currency}"


CATEGORY_EMOJIS = {
    "Makanan & Minuman": "🍔",
    "Transportasi": "🚗",
    "Belanja": "🛒",
    "Tagihan & Utilitas": "💡",
    "Hiburan": "🎮",
    "Kesehatan": "🏥",
    "Lainnya": "📦",
}


# ── Command Handlers ────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message & register user."""
    user = update.effective_user
    if not user:
        return

    # Register user in database
    async with get_session() as session:
        user_repo = UserRepository(session)
        await user_repo.get_or_create(
            platform_id=str(user.id),
            platform="telegram",
            name=user.first_name or user.username,
        )

    welcome_text = (
        f"👋 Halo {user.first_name}! Selamat datang di *Jarfin*.\n\n"
        f"Aku bot pencatat keuangan kamu. Ini yang bisa aku lakukan:\n\n"
        f"💬 *Kirim pesan teks* — Catat transaksi\n"
        f"   Contoh: _Makan siang warteg 25000_\n\n"
        f"📷 *Kirim foto struk* — Aku baca otomatis pakai AI\n\n"
        f"📊 /ringkasan — Lihat ringkasan keuangan\n"
        f"📝 /riwayat — Lihat riwayat transaksi\n"
        f"💰 /budget — Kelola budget\n"
        f"🔄 /rutin — Transaksi rutin bulanan\n"
        f"⚙️ /settings — Pengaturan\n"
        f"❓ /help — Bantuan lebih lengkap\n\n"
        f"Mulai catat transaksimu sekarang! 🚀"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=build_main_menu_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show available commands and usage."""
    help_text = (
        "📖 *Panduan Jarfin*\n\n"
        "*Cara Mencatat Transaksi:*\n"
        "1️⃣ Ketik langsung, contoh:\n"
        "   • _Kopi Starbucks 55000_\n"
        "   • _Grab ke kantor 25000_\n"
        "   • _Bayar listrik 350000_\n\n"
        "2️⃣ Kirim foto struk/nota\n"
        "   Bot akan membaca otomatis pakai AI\n\n"
        "*Perintah:*\n"
        "• /start — Mulai bot\n"
        "• /help — Tampilkan bantuan ini\n"
        "• /ringkasan — Ringkasan bulan ini\n"
        "• /riwayat — Riwayat transaksi\n"
        "• /hapus `<id>` — Hapus transaksi berdasarkan ID\n"
        "• /budget — Kelola budget per kategori\n"
        "• /rutin — Kelola transaksi rutin\n"
        "• /settings — Pengaturan mata uang & zona waktu\n\n"
        "*Kategori Otomatis:*\n"
        "🍔 Makanan & Minuman\n"
        "🚗 Transportasi\n"
        "🛒 Belanja\n"
        "💡 Tagihan & Utilitas\n"
        "🎮 Hiburan\n"
        "🏥 Kesehatan\n"
        "📦 Lainnya\n\n"
        "─────────────────────\n"
        "🇬🇧 *English*\n"
        "_Just type your transaction in English too!_\n"
        "_Example: Coffee at Starbucks 55000_"
    )

    await update.message.reply_text(help_text, parse_mode="Markdown")


async def ringkasan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ringkasan — show monthly spending summary."""
    user = update.effective_user
    if not user:
        return

    await _send_summary(update.message, str(user.id))


async def riwayat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /riwayat — show transaction history.

    Usage:
      /riwayat            → inline keyboard to choose period
      /riwayat hari ini   → today's transactions
      /riwayat minggu ini → this week
      /riwayat bulan ini  → this month
      /riwayat 2026-06    → specific month
    """
    user = update.effective_user
    if not user:
        return

    args_text = " ".join(context.args) if context.args else ""

    if not args_text:
        # Show period selection keyboard
        await update.message.reply_text(
            "📝 *Riwayat Transaksi*\n\nPilih periode:",
            parse_mode="Markdown",
            reply_markup=build_history_period_keyboard(),
        )
        return

    await _send_history(update.message, str(user.id), args_text.strip().lower())


async def hapus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /hapus <id> — delete a transaction by ID."""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ *Format salah.*\n\n"
            "Gunakan: `/hapus <id_transaksi>`\n"
            "Contoh: `/hapus 42`\n\n"
            "ID transaksi bisa dilihat di /riwayat.",
            parse_mode="Markdown",
        )
        return

    try:
        txn_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ ID transaksi harus berupa angka.\nContoh: `/hapus 42`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"⚠️ *Konfirmasi Hapus*\n\n"
        f"Kamu yakin ingin menghapus transaksi *#{txn_id}*?\n"
        f"Tindakan ini tidak bisa dibatalkan.",
        parse_mode="Markdown",
        reply_markup=build_delete_confirm_keyboard(txn_id),
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings — show user settings."""
    user = update.effective_user
    if not user:
        return

    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_by_platform_id(str(user.id))

    if not db_user:
        await update.message.reply_text("❌ Kamu belum terdaftar. Kirim /start dulu ya!")
        return

    settings_text = (
        "⚙️ *Pengaturan*\n\n"
        f"👤 Nama: {db_user.name or '-'}\n"
        f"💱 Mata Uang: *{db_user.currency}*\n"
        f"🕐 Zona Waktu: *{db_user.timezone}*\n\n"
        "Pilih yang ingin diubah:"
    )

    await update.message.reply_text(
        settings_text,
        parse_mode="Markdown",
        reply_markup=build_settings_keyboard(),
    )


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /budget — budget management."""
    user = update.effective_user
    if not user:
        return

    await update.message.reply_text(
        "💰 *Budget Manager*\n\nPilih opsi:",
        parse_mode="Markdown",
        reply_markup=build_budget_menu_keyboard(),
    )


async def rutin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /rutin — recurring transactions management."""
    user = update.effective_user
    if not user:
        return

    await update.message.reply_text(
        "🔄 *Transaksi Rutin*\n\nKelola transaksi yang otomatis tercatat secara berkala.",
        parse_mode="Markdown",
        reply_markup=build_recurring_menu_keyboard(),
    )


# ── Message Handlers ────────────────────────────────────────────────────────

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages — parse as transaction input with confirmation flow.
    If the message is not a transaction (low confidence), respond conversationally.
    """
    user = update.effective_user
    message = update.message
    if not user or not message or not message.text:
        return

    text = message.text.strip()
    if not text:
        return

    # Check if user is in budget-set mode (waiting for amount input)
    if context.user_data.get("awaiting_budget_amount"):
        await _handle_budget_amount_input(update, context, text)
        return

    # Check if user is in recurring setup mode
    if context.user_data.get("awaiting_recurring_amount"):
        await _handle_recurring_amount_input(update, context, text)
        return

    if context.user_data.get("awaiting_recurring_description"):
        await _handle_recurring_description_input(update, context, text)
        return

    # Send "typing" indicator
    await message.reply_chat_action("typing")

    try:
        # Parse WITHOUT saving (confidence check first)
        result = await _transaction_service.parse_only(text)

        # ── Confidence threshold: if < 0.3, treat as casual conversation ──
        # This catches greetings, questions, or unrelated messages.
        if result["confidence"] < 0.3:
            reply = await _ai_provider.chat_response(
                text=text,
                user_name=user.first_name or user.username,
            )
            await message.reply_text(reply, parse_mode="Markdown")
            return

        # ── High enough confidence → show transaction preview ──
        pending_id = str(uuid.uuid4())[:8]

        # Store in user_data for later confirmation
        context.user_data[f"pending_{pending_id}"] = {
            **result,
            "platform_id": str(user.id),
            "user_name": user.first_name or user.username,
            "source": "text",
        }

        # Build preview message
        confidence_emoji = "🟢" if result["confidence"] >= 0.7 else "🟡" if result["confidence"] >= 0.4 else "🔴"

        type_str = "📥 Pemasukan" if result.get("type") == "income" else "📤 Pengeluaran"
        preview_text = (
            f"📝 *Preview Transaksi*\n\n"
            f"💰 Nominal: *{_format_currency(result['amount'])}*\n"
            f"🏷️ Jenis: {type_str}\n"
            f"📁 Kategori: {result['category'] or 'Lainnya'}\n"
        )

        if result.get("merchant"):
            preview_text += f"🏪 Merchant: {result['merchant']}\n"
        if result.get("description"):
            preview_text += f"📝 Deskripsi: {result['description']}\n"

        preview_text += (
            f"📅 Tanggal: {result['date']}\n"
            f"{confidence_emoji} Confidence: {result['confidence']:.0%}\n\n"
            f"_Simpan transaksi ini?_"
        )

        await message.reply_text(
            preview_text,
            parse_mode="Markdown",
            reply_markup=build_confirm_keyboard(pending_id),
        )

    except Exception as e:
        logger.error(f"Error processing text message: {e}", exc_info=True)
        await message.reply_text(
            "❌ Maaf, terjadi kesalahan saat memproses pesan kamu.\n"
            "Coba lagi atau ketik /help untuk bantuan."
        )




async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages — download photo and process receipt."""
    user = update.effective_user
    message = update.message
    if not user or not message or not message.photo:
        return

    # Send "processing" indicator
    await message.reply_chat_action("typing")

    try:
        # Get the largest photo (last in the array)
        photo = message.photo[-1]
        photo_file = await photo.get_file()

        # Download photo bytes
        photo_bytes = await photo_file.download_as_bytearray()

        # For Qwen: skip OCR, use direct image parsing
        ocr_text = None
        if settings.ai_provider != "qwen":
            ocr_text = await _ocr_service.extract_text(bytes(photo_bytes))
            if not ocr_text:
                await message.reply_text(
                    "📷 Foto diterima! Tapi OCR belum aktif.\n\n"
                    "⏳ Fitur baca struk otomatis akan segera hadir.\n"
                    "Untuk sekarang, kamu bisa ketik manual:\n"
                    "_Contoh: Belanja Indomaret 85000_",
                    parse_mode="Markdown",
                )
                return

        # Process via service (Qwen parses directly; others use OCR fallback)
        results = await _transaction_service.process_photo_message(
            platform_id=str(user.id),
            ocr_text=ocr_text,
            image_bytes=bytes(photo_bytes),
            image_url=photo_file.file_path,
            platform="telegram",
            user_name=user.first_name or user.username,
        )

        if not results:
            await message.reply_text(
                "📷 Foto diterima, tapi tidak bisa membaca transaksi dari struk.\n"
                "Coba kirim foto yang lebih jelas atau ketik manual."
            )
            return

        # Build result message
        lines = [
            "✅ *Transaksi Berhasil Disimpan!*\n",
            f"📷 *{len(results)} transaksi terdeteksi dari struk:*\n"
        ]
        total = 0.0

        for i, r in enumerate(results, 1):
            total += r["amount"]
            lines.append(
                f"{i}. {r.get('description', '-')}: *{_format_currency(r['amount'])}*"
                f" [{r['category']}]"
            )

        lines.append(f"\n💰 *Total: {_format_currency(total)}*")

        await message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error processing photo message: {e}", exc_info=True)
        await message.reply_text(
            "❌ Maaf, terjadi kesalahan saat memproses foto.\n"
            "Coba kirim ulang atau ketik transaksi secara manual."
        )


# ── Callback Query Handler ──────────────────────────────────────────────────

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all inline keyboard button presses."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()  # Acknowledge the button press

    data = query.data
    user = update.effective_user

    if not user:
        return

    platform_id = str(user.id)

    try:
        # Noop button (e.g., page indicator)
        if data == "noop":
            return

        # ── Menu navigation ─────────────────────────────────────────────
        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            await _handle_menu_callback(query, context, platform_id, action)

        # ── Transaction confirmation ────────────────────────────────────
        elif data.startswith("confirm:"):
            pending_id = data.split(":", 1)[1]
            await _handle_confirm_callback(query, context, platform_id, pending_id)

        elif data.startswith("cancel:"):
            pending_id = data.split(":", 1)[1]
            await _handle_cancel_callback(query, context, pending_id)

        # ── Category edit (for pending transactions) ─────────────────────
        elif data.startswith("editcat:"):
            pending_id = data.split(":", 1)[1]
            await _handle_editcat_callback(query, context, pending_id)

        elif data.startswith("cat:"):
            cat_name = data.split(":", 1)[1]
            await _handle_category_select_callback(query, context, platform_id, cat_name)

        # ── Transaction delete ──────────────────────────────────────────
        elif data.startswith("delete_req:"):
            txn_id = int(data.split(":", 1)[1])
            await _handle_delete_req_callback(query, context, platform_id, txn_id)

        elif data.startswith("delete_confirm:"):
            txn_id = int(data.split(":", 1)[1])
            await _handle_delete_confirm_callback(query, context, platform_id, txn_id)

        # ── Settings ────────────────────────────────────────────────────
        elif data.startswith("settings:"):
            setting = data.split(":", 1)[1]
            await _handle_settings_callback(query, context, platform_id, setting)

        elif data.startswith("setcurrency:"):
            currency = data.split(":", 1)[1]
            await _handle_set_currency(query, context, platform_id, currency)

        elif data.startswith("settz:"):
            timezone = data.split(":", 1)[1]
            await _handle_set_timezone(query, context, platform_id, timezone)

        # ── Budget ──────────────────────────────────────────────────────
        elif data.startswith("budget:"):
            action = data.split(":", 1)[1]
            await _handle_budget_callback(query, context, platform_id, action)

        elif data.startswith("budgetcat:"):
            cat_name = data.split(":", 1)[1]
            await _handle_budget_category_select(query, context, platform_id, cat_name)

        elif data.startswith("budget_del:"):
            category = data.split(":", 1)[1]
            await _handle_budget_delete_select(query, context, platform_id, category)

        elif data.startswith("budget_del_confirm:"):
            category = data.split(":", 1)[1]
            await _handle_budget_delete_confirm(query, context, platform_id, category)

        # ── History ─────────────────────────────────────────────────────
        elif data.startswith("history:"):
            period = data.split(":", 1)[1]
            await _handle_history_callback(query, context, platform_id, period)

        elif data.startswith("histpage:"):
            # histpage:<period>:<page>
            parts = data.split(":")
            # period may contain colons (e.g. "2026-06") so join middle parts
            period = parts[1]
            page = int(parts[2])
            await _send_history(query.message, platform_id, period, page=page, edit=True)

        # ── Recurring ───────────────────────────────────────────────────
        elif data.startswith("recurring:"):
            action = data.split(":", 1)[1]
            await _handle_recurring_callback(query, context, platform_id, action)

        elif data.startswith("reccat:"):
            cat_name = data.split(":", 1)[1]
            await _handle_recurring_category_select(query, context, platform_id, cat_name)

        elif data.startswith("recfreq:"):
            frequency = data.split(":", 1)[1]
            await _handle_recurring_frequency_select(query, context, platform_id, frequency)

        elif data.startswith("rec_delete:"):
            rec_id = int(data.split(":", 1)[1])
            await _handle_recurring_delete(query, context, platform_id, rec_id)

        elif data.startswith("rec_exec:"):
            rec_id = int(data.split(":", 1)[1])
            await _handle_recurring_exec(query, context, platform_id, rec_id)

        else:
            logger.warning(f"Unhandled callback data: {data}")

    except Exception as e:
        logger.error(f"Error handling callback '{data}': {e}", exc_info=True)
        await query.message.reply_text("❌ Terjadi kesalahan. Coba lagi.")


# ── Callback Handler Implementations ────────────────────────────────────────

async def _handle_menu_callback(query, context, platform_id: str, action: str) -> None:
    """Handle main menu button presses."""
    if action == "summary":
        await _send_summary(query.message, platform_id, edit=True)

    elif action == "history":
        await query.message.edit_text(
            "📝 *Riwayat Transaksi*\n\nPilih periode:",
            parse_mode="Markdown",
            reply_markup=build_history_period_keyboard(),
        )

    elif action == "budget":
        await query.message.edit_text(
            "💰 *Budget Manager*\n\nPilih opsi:",
            parse_mode="Markdown",
            reply_markup=build_budget_menu_keyboard(),
        )

    elif action == "recurring":
        await query.message.edit_text(
            "🔄 *Transaksi Rutin*\n\nKelola transaksi yang otomatis tercatat secara berkala.",
            parse_mode="Markdown",
            reply_markup=build_recurring_menu_keyboard(),
        )

    elif action == "settings":
        async with get_session() as session:
            user_repo = UserRepository(session)
            db_user = await user_repo.get_by_platform_id(platform_id)

        if db_user:
            text = (
                "⚙️ *Pengaturan*\n\n"
                f"👤 Nama: {db_user.name or '-'}\n"
                f"💱 Mata Uang: *{db_user.currency}*\n"
                f"🕐 Zona Waktu: *{db_user.timezone}*\n\n"
                "Pilih yang ingin diubah:"
            )
        else:
            text = "⚙️ *Pengaturan*\n\nPilih yang ingin diubah:"

        await query.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(),
        )

    elif action == "help":
        help_text = (
            "📖 *Panduan Singkat*\n\n"
            "💬 Ketik transaksi → _Kopi 15000_\n"
            "📷 Kirim foto struk\n"
            "📊 /ringkasan → Ringkasan bulan ini\n"
            "📝 /riwayat → Riwayat transaksi\n"
            "💰 /budget → Kelola budget\n"
            "🔄 /rutin → Transaksi rutin\n"
            "⚙️ /settings → Pengaturan"
        )
        await query.message.edit_text(
            help_text,
            parse_mode="Markdown",
            reply_markup=build_main_menu_keyboard(),
        )

    elif action == "main":
        await query.message.edit_text(
            "🏠 *Menu Utama*\n\nPilih opsi:",
            parse_mode="Markdown",
            reply_markup=build_main_menu_keyboard(),
        )


async def _handle_confirm_callback(query, context, platform_id: str, pending_id: str) -> None:
    """Handle transaction confirmation."""
    key = f"pending_{pending_id}"
    pending = context.user_data.get(key)

    if not pending:
        await query.message.edit_text("⚠️ Transaksi sudah kadaluarsa. Kirim ulang pesan kamu.")
        return

    # Save to DB
    result = await _transaction_service.save_parsed(
        platform_id=pending["platform_id"],
        parsed_data=pending,
        source=pending.get("source", "text"),
        platform="telegram",
        user_name=pending.get("user_name"),
    )

    # Clean up
    del context.user_data[key]

    # Check budget alert
    category = result.get("category")
    alert = None
    if category and result.get("type", "expense") == "expense":
        alert = await _budget_service.check_budget_alert(platform_id, category)

    # Build success message
    type_str = "📥 Pemasukan" if result.get("type") == "income" else "📤 Pengeluaran"
    confirm_text = (
        f"✅ *Transaksi Disimpan!*\n\n"
        f"💰 Nominal: *{_format_currency(result['amount'])}*\n"
        f"🏷️ Jenis: {type_str}\n"
        f"📁 Kategori: {result['category'] or 'Lainnya'}\n"
    )

    if result.get("merchant"):
        confirm_text += f"🏪 Merchant: {result['merchant']}\n"
    if result.get("description"):
        confirm_text += f"📝 Deskripsi: {result['description']}\n"

    confirm_text += f"📅 Tanggal: {result['date']}\n"
    confirm_text += f"🆔 ID: #{result['transaction_id']}"

    # Append budget alert if applicable
    if alert:
        if alert["level"] == "exceeded":
            confirm_text += (
                f"\n\n🔴 *BUDGET EXCEEDED!*\n"
                f"Kategori {alert['category']}: "
                f"{_format_currency(alert['spent'])} / {_format_currency(alert['budget'])} "
                f"({alert['percentage']:.0f}%)"
            )
        elif alert["level"] == "warning":
            confirm_text += (
                f"\n\n🟡 *Budget Warning*\n"
                f"Kategori {alert['category']}: "
                f"{_format_currency(alert['spent'])} / {_format_currency(alert['budget'])} "
                f"({alert['percentage']:.0f}%)"
            )

    await query.message.edit_text(confirm_text, parse_mode="Markdown")


async def _handle_cancel_callback(query, context, pending_id: str) -> None:
    """Handle transaction cancellation."""
    key = f"pending_{pending_id}"
    if key in context.user_data:
        del context.user_data[key]

    await query.message.edit_text("❌ Transaksi dibatalkan.")


async def _handle_editcat_callback(query, context, pending_id: str) -> None:
    """Show category selection keyboard for editing a pending transaction."""
    key = f"pending_{pending_id}"
    pending = context.user_data.get(key)

    if not pending:
        await query.message.edit_text("⚠️ Transaksi sudah kadaluarsa. Kirim ulang pesan kamu.")
        return

    # Store which pending transaction we're editing
    context.user_data["editing_category_for"] = pending_id

    await query.message.edit_text(
        f"✏️ *Edit Kategori*\n\n"
        f"Transaksi: {pending.get('description', '-')} — {_format_currency(pending['amount'])}\n\n"
        f"Pilih kategori baru:",
        parse_mode="Markdown",
        reply_markup=build_category_keyboard(),
    )


async def _handle_category_select_callback(query, context, platform_id: str, cat_name: str) -> None:
    """Handle category selection for a pending transaction."""
    pending_id = context.user_data.get("editing_category_for")
    if not pending_id:
        await query.message.edit_text("⚠️ Tidak ada transaksi yang sedang diedit.")
        return

    key = f"pending_{pending_id}"
    pending = context.user_data.get(key)

    if not pending:
        await query.message.edit_text("⚠️ Transaksi sudah kadaluarsa.")
        del context.user_data["editing_category_for"]
        return

    # Update category
    pending["category"] = cat_name
    context.user_data[key] = pending
    del context.user_data["editing_category_for"]

    # Show updated preview
    emoji = CATEGORY_EMOJIS.get(cat_name, "📌")
    type_str = "📥 Pemasukan" if pending.get("type") == "income" else "📤 Pengeluaran"
    preview_text = (
        f"📝 *Preview Transaksi (Updated)*\n\n"
        f"💰 Nominal: *{_format_currency(pending['amount'])}*\n"
        f"🏷️ Jenis: {type_str}\n"
        f"📁 Kategori: {emoji} {cat_name}\n"
    )

    if pending.get("merchant"):
        preview_text += f"🏪 Merchant: {pending['merchant']}\n"
    if pending.get("description"):
        preview_text += f"📝 Deskripsi: {pending['description']}\n"

    preview_text += (
        f"📅 Tanggal: {pending['date']}\n\n"
        f"_Simpan transaksi ini?_"
    )

    await query.message.edit_text(
        preview_text,
        parse_mode="Markdown",
        reply_markup=build_confirm_keyboard(pending_id),
    )


# ── Delete Transaction Callbacks ────────────────────────────────────────────

async def _handle_delete_req_callback(query, context, platform_id: str, txn_id: int) -> None:
    """Show delete confirmation for a transaction."""
    await query.message.edit_text(
        f"⚠️ *Konfirmasi Hapus*\n\n"
        f"Kamu yakin ingin menghapus transaksi *#{txn_id}*?\n"
        f"Tindakan ini tidak bisa dibatalkan.",
        parse_mode="Markdown",
        reply_markup=build_delete_confirm_keyboard(txn_id),
    )


async def _handle_delete_confirm_callback(query, context, platform_id: str, txn_id: int) -> None:
    """Execute transaction deletion after confirmation."""
    result = await _transaction_service.delete_transaction(platform_id, txn_id)

    if "error" in result:
        await query.message.edit_text(
            f"❌ Gagal menghapus transaksi: {result['error']}"
        )
        return

    await query.message.edit_text(
        f"✅ *Transaksi #{txn_id} berhasil dihapus.*",
        parse_mode="Markdown",
        reply_markup=build_history_period_keyboard(),
    )


# ── Settings Callback Handlers ──────────────────────────────────────────────

async def _handle_settings_callback(query, context, platform_id: str, setting: str) -> None:
    """Handle settings sub-menu selections."""
    if setting == "currency":
        await query.message.edit_text(
            "💱 *Pilih Mata Uang*\n\nPilih mata uang default:",
            parse_mode="Markdown",
            reply_markup=build_currency_keyboard(),
        )
    elif setting == "timezone":
        await query.message.edit_text(
            "🕐 *Pilih Zona Waktu*\n\nPilih zona waktu:",
            parse_mode="Markdown",
            reply_markup=build_timezone_keyboard(),
        )


async def _handle_set_currency(query, context, platform_id: str, currency: str) -> None:
    """Handle currency selection."""
    async with get_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.update_settings(platform_id, currency=currency)

    if user:
        await query.message.edit_text(
            f"✅ Mata uang berhasil diubah ke *{currency}*",
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(),
        )
    else:
        await query.message.edit_text("❌ Gagal mengubah pengaturan.")


async def _handle_set_timezone(query, context, platform_id: str, timezone: str) -> None:
    """Handle timezone selection."""
    async with get_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.update_settings(platform_id, timezone=timezone)

    if user:
        await query.message.edit_text(
            f"✅ Zona waktu berhasil diubah ke *{timezone}*",
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(),
        )
    else:
        await query.message.edit_text("❌ Gagal mengubah pengaturan.")


# ── Budget Callback Handlers ────────────────────────────────────────────────

async def _handle_budget_callback(query, context, platform_id: str, action: str) -> None:
    """Handle budget menu actions."""
    if action == "view":
        await _send_budget_overview(query.message, platform_id, edit=True)

    elif action == "set":
        await query.message.edit_text(
            "➕ *Set Budget*\n\nPilih kategori yang ingin di-budget:",
            parse_mode="Markdown",
            reply_markup=build_budget_category_keyboard(),
        )

    elif action == "delete_select":
        await query.message.edit_text(
            "🗑️ *Hapus Budget*\n\nPilih kategori yang ingin dihapus:",
            parse_mode="Markdown",
            reply_markup=build_budget_delete_keyboard(),
        )

    elif action == "copy_next":
        result = await _budget_service.copy_to_next_month(platform_id)
        if "error" in result:
            await query.message.edit_text(f"❌ {result['error']}")
        elif result["copied"] == 0:
            await query.message.edit_text(
                f"ℹ️ Tidak ada budget yang di-copy.\n\n"
                f"Kemungkinan budget bulan {result['next_month']} sudah ada, "
                f"atau belum ada budget di bulan ini.",
                reply_markup=build_budget_menu_keyboard(),
            )
        else:
            await query.message.edit_text(
                f"✅ *Budget Di-copy!*\n\n"
                f"📋 {result['copied']} budget berhasil di-copy ke *{result['next_month']}*.",
                parse_mode="Markdown",
                reply_markup=build_budget_menu_keyboard(),
            )


async def _handle_budget_category_select(query, context, platform_id: str, cat_name: str) -> None:
    """Handle budget category selection — ask for amount."""
    context.user_data["awaiting_budget_amount"] = True
    context.user_data["budget_category"] = cat_name
    context.user_data["budget_message_id"] = query.message.message_id

    emoji = CATEGORY_EMOJIS.get(cat_name, "📌")
    await query.message.edit_text(
        f"💰 *Set Budget — {emoji} {cat_name}*\n\n"
        f"Ketik jumlah budget bulanan (angka saja):\n"
        f"_Contoh: 500000_",
        parse_mode="Markdown",
    )


async def _handle_budget_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle budget amount input from user."""
    import re

    # Clean the input — remove non-numeric chars except dots/commas
    cleaned = re.sub(r"[^\d]", "", text)

    if not cleaned:
        await update.message.reply_text(
            "⚠️ Masukkan angka yang valid.\n_Contoh: 500000_",
            parse_mode="Markdown",
        )
        return

    amount = float(cleaned)
    category = context.user_data.get("budget_category", "Lainnya")
    platform_id = str(update.effective_user.id)

    # Save budget
    result = await _budget_service.set_budget(
        platform_id=platform_id,
        category=category,
        amount=amount,
    )

    # Clear state
    context.user_data.pop("awaiting_budget_amount", None)
    context.user_data.pop("budget_category", None)
    context.user_data.pop("budget_message_id", None)

    if "error" in result:
        await update.message.reply_text("❌ Gagal menyimpan budget. Kirim /start dulu.")
        return

    emoji = CATEGORY_EMOJIS.get(category, "📌")
    await update.message.reply_text(
        f"✅ *Budget Disimpan!*\n\n"
        f"{emoji} {category}: *{_format_currency(amount)}* / bulan\n"
        f"📅 Periode: {result['month']}",
        parse_mode="Markdown",
        reply_markup=build_budget_menu_keyboard(),
    )


async def _handle_budget_delete_select(query, context, platform_id: str, category: str) -> None:
    """Show delete confirmation for a budget category."""
    emoji = CATEGORY_EMOJIS.get(category, "📌")
    await query.message.edit_text(
        f"🗑️ *Hapus Budget — {emoji} {category}*\n\n"
        f"Kamu yakin ingin menghapus budget untuk kategori ini?\n"
        f"Tindakan ini tidak bisa dibatalkan.",
        parse_mode="Markdown",
        reply_markup=build_budget_delete_confirm_keyboard(category),
    )


async def _handle_budget_delete_confirm(query, context, platform_id: str, category: str) -> None:
    """Execute budget deletion after confirmation."""
    result = await _budget_service.delete_budget(platform_id, category)

    if "error" in result:
        await query.message.edit_text(f"❌ {result['error']}")
        return

    emoji = CATEGORY_EMOJIS.get(category, "📌")
    await query.message.edit_text(
        f"✅ Budget {emoji} *{category}* berhasil dihapus.",
        parse_mode="Markdown",
        reply_markup=build_budget_menu_keyboard(),
    )


# ── History Callback Handler ────────────────────────────────────────────────

async def _handle_history_callback(query, context, platform_id: str, period: str) -> None:
    """Handle history period selection."""
    await _send_history(query.message, platform_id, period, page=0, edit=True)


# ── Recurring Callback Handlers ─────────────────────────────────────────────

async def _handle_recurring_callback(query, context, platform_id: str, action: str) -> None:
    """Handle recurring menu actions."""
    if action == "list":
        await _send_recurring_list(query.message, platform_id, edit=True)

    elif action == "add":
        context.user_data["recurring_setup"] = {}
        await query.message.edit_text(
            "🔄 *Tambah Transaksi Rutin*\n\nLangkah 1/3: Pilih kategori:",
            parse_mode="Markdown",
            reply_markup=build_recurring_category_keyboard(),
        )


async def _handle_recurring_category_select(query, context, platform_id: str, cat_name: str) -> None:
    """Store category selection and ask for frequency."""
    context.user_data.setdefault("recurring_setup", {})
    context.user_data["recurring_setup"]["category"] = cat_name

    emoji = CATEGORY_EMOJIS.get(cat_name, "📌")
    await query.message.edit_text(
        f"🔄 *Tambah Transaksi Rutin*\n\n"
        f"📁 Kategori: {emoji} {cat_name}\n\n"
        f"Langkah 2/3: Pilih frekuensi:",
        parse_mode="Markdown",
        reply_markup=build_recurring_frequency_keyboard(),
    )


async def _handle_recurring_frequency_select(query, context, platform_id: str, frequency: str) -> None:
    """Store frequency and ask for amount."""
    context.user_data.setdefault("recurring_setup", {})
    context.user_data["recurring_setup"]["frequency"] = frequency

    freq_labels = {"daily": "Harian", "weekly": "Mingguan", "monthly": "Bulanan"}
    cat_name = context.user_data["recurring_setup"].get("category", "?")
    emoji = CATEGORY_EMOJIS.get(cat_name, "📌")

    context.user_data["awaiting_recurring_amount"] = True

    await query.message.edit_text(
        f"🔄 *Tambah Transaksi Rutin*\n\n"
        f"📁 Kategori: {emoji} {cat_name}\n"
        f"📅 Frekuensi: {freq_labels.get(frequency, frequency)}\n\n"
        f"Langkah 3/3: Ketik nominal (angka saja):\n_Contoh: 350000_",
        parse_mode="Markdown",
    )


async def _handle_recurring_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle recurring transaction amount input."""
    import re
    cleaned = re.sub(r"[^\d]", "", text)
    if not cleaned:
        await update.message.reply_text(
            "⚠️ Masukkan angka yang valid.\n_Contoh: 350000_",
            parse_mode="Markdown",
        )
        return

    context.user_data["recurring_setup"]["amount"] = float(cleaned)
    context.user_data.pop("awaiting_recurring_amount", None)
    context.user_data["awaiting_recurring_description"] = True

    await update.message.reply_text(
        f"🔄 *Tambah Transaksi Rutin*\n\n"
        f"Nominal: *{_format_currency(float(cleaned))}*\n\n"
        f"Terakhir: Ketik deskripsi singkat untuk transaksi ini:\n"
        f"_Contoh: Bayar listrik PLN_",
        parse_mode="Markdown",
    )


async def _handle_recurring_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle recurring transaction description input — finalize creation."""
    setup = context.user_data.get("recurring_setup", {})
    platform_id = str(update.effective_user.id)

    result = await _recurring_service.create_recurring(
        platform_id=platform_id,
        amount=setup.get("amount", 0),
        description=text,
        category=setup.get("category", "Lainnya"),
        frequency=setup.get("frequency", "monthly"),
    )

    context.user_data.pop("awaiting_recurring_description", None)
    context.user_data.pop("recurring_setup", None)

    if "error" in result:
        await update.message.reply_text(f"❌ {result['error']}")
        return

    emoji = CATEGORY_EMOJIS.get(result["category"], "📌")
    await update.message.reply_text(
        f"✅ *Transaksi Rutin Ditambahkan!*\n\n"
        f"{emoji} {result['category']}: *{_format_currency(result['amount'])}*\n"
        f"📝 {result['description']}\n"
        f"📅 Frekuensi: {result['frequency']}\n"
        f"⏭️ Eksekusi berikutnya: {result['next_run_date']}",
        parse_mode="Markdown",
        reply_markup=build_recurring_menu_keyboard(),
    )


async def _handle_recurring_delete(query, context, platform_id: str, rec_id: int) -> None:
    """Delete a recurring transaction."""
    result = await _recurring_service.delete_recurring(platform_id, rec_id)

    if "error" in result:
        await query.message.edit_text(f"❌ {result['error']}")
        return

    await query.message.edit_text(
        f"✅ Transaksi rutin #{rec_id} dihapus.",
        reply_markup=build_recurring_menu_keyboard(),
    )


async def _handle_recurring_exec(query, context, platform_id: str, rec_id: int) -> None:
    """Manually execute a recurring transaction now."""
    result = await _recurring_service.execute_now(platform_id, rec_id)

    if "error" in result:
        await query.message.edit_text(f"❌ {result['error']}")
        return

    await query.message.edit_text(
        f"✅ *Transaksi Dieksekusi!*\n\n"
        f"💰 *{_format_currency(result['amount'])}* berhasil dicatat.\n"
        f"🆔 ID Transaksi: #{result['transaction_id']}\n"
        f"⏭️ Jadwal berikutnya: {result['next_run_date']}",
        parse_mode="Markdown",
        reply_markup=build_recurring_menu_keyboard(),
    )


# ── Shared Display Functions ────────────────────────────────────────────────

async def _send_summary(message, platform_id: str, edit: bool = False) -> None:
    """Send spending summary. Used by both command and callback."""
    summary = await _transaction_service.get_summary(platform_id=platform_id)

    if "error" in summary:
        text = "❌ Kamu belum terdaftar. Kirim /start dulu ya!"
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return

    total_expenses = summary.get("total_expenses", 0.0)
    total_income = summary.get("total_income", 0.0)
    balance = total_income - total_expenses

    if total_expenses == 0 and total_income == 0:
        text = (
            f"📊 *Ringkasan {summary['month']}*\n\n"
            f"Belum ada transaksi bulan ini.\n"
            f"Mulai catat dengan mengirim pesan! 💬"
        )
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=build_main_menu_keyboard())
        else:
            await message.reply_text(text, parse_mode="Markdown", reply_markup=build_main_menu_keyboard())
        return

    # Build summary text
    balance_emoji = "📈" if balance >= 0 else "📉"
    lines = [
        f"📊 *Ringkasan {summary['month']}*\n",
        f"📥 Total Pemasukan: *{_format_currency(total_income)}*",
        f"📤 Total Pengeluaran: *{_format_currency(total_expenses)}*",
        f"{balance_emoji} Saldo Net: *{_format_currency(balance)}*\n",
    ]

    # Income breakdown (if any)
    income_cats = summary.get("income_categories", {})
    if income_cats:
        lines.append("*📥 Rincian Pemasukan:*")
        for cat_name, data in income_cats.items():
            emoji = CATEGORY_EMOJIS.get(cat_name, "📌")
            lines.append(
                f"{emoji} {cat_name}: {_format_currency(data['total'])} ({data['count']}x)"
            )
        lines.append("")

    # Expense breakdown
    lines.append("*📤 Rincian Pengeluaran per Kategori:*")
    if not summary["categories"]:
        lines.append(" _Belum ada rincian pengeluaran._")
    else:
        for cat_name, data in summary["categories"].items():
            emoji = CATEGORY_EMOJIS.get(cat_name, "📌")
            lines.append(
                f"{emoji} {cat_name}: {_format_currency(data['total'])} ({data['count']}x)"
            )

    text = "\n".join(lines)
    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=build_main_menu_keyboard())
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=build_main_menu_keyboard())


async def _send_history(
    message,
    platform_id: str,
    period: str = "recent",
    page: int = 0,
    edit: bool = False,
) -> None:
    """Send transaction history with pagination. Used by both command and callback."""
    history = await _transaction_service.get_history(
        platform_id=platform_id, period=period, page=page
    )

    if "error" in history:
        text = "❌ Kamu belum terdaftar. Kirim /start dulu ya!"
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return

    period_labels = {
        "recent": "Terbaru",
        "hari ini": "Hari Ini",
        "minggu ini": "Minggu Ini",
        "bulan ini": "Bulan Ini",
    }
    label = period_labels.get(period, period)
    total_count = history.get("total_count", history["count"])
    total_pages = history.get("total_pages", 1)

    if history["count"] == 0:
        text = f"📝 *Riwayat — {label}*\n\nBelum ada transaksi."
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=build_history_period_keyboard())
        else:
            await message.reply_text(text, parse_mode="Markdown", reply_markup=build_history_period_keyboard())
        return

    lines = [f"📝 *Riwayat — {label}* ({total_count} transaksi)\n"]

    total_income = 0.0
    total_expense = 0.0
    for t in history["transactions"]:
        is_income = t.get("type") == "income"
        type_prefix = "📥" if is_income else "📤"
        if is_income:
            total_income += t["amount"]
        else:
            total_expense += t["amount"]

        emoji = CATEGORY_EMOJIS.get(t["category"], "📌")
        desc = t.get("description") or t.get("merchant") or "-"
        lines.append(
            f"{type_prefix} {emoji} {desc}: *{_format_currency(t['amount'])}*"
            f"\n     📅 {t['date']} | 🆔 #{t['id']}"
        )

    lines.append(
        f"\n📥 Pemasukan: *{_format_currency(total_income)}*"
        f"\n📤 Pengeluaran: *{_format_currency(total_expense)}*"
    )

    text = "\n".join(lines)

    # Use pagination keyboard if more than one page
    if total_pages > 1:
        keyboard = build_history_nav_keyboard(period, page, total_pages)
    else:
        keyboard = build_history_period_keyboard()

    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def _send_budget_overview(message, platform_id: str, edit: bool = False) -> None:
    """Send budget overview. Used by both command and callback."""
    overview = await _budget_service.get_overview(platform_id=platform_id)

    if "error" in overview:
        text = "❌ Kamu belum terdaftar. Kirim /start dulu ya!"
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return

    if not overview["budgets"]:
        text = (
            f"💰 *Budget — {overview['month']}*\n\n"
            f"Belum ada budget yang di-set.\n"
            f"Tekan *Set Budget* untuk mulai."
        )
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=build_budget_menu_keyboard())
        else:
            await message.reply_text(text, parse_mode="Markdown", reply_markup=build_budget_menu_keyboard())
        return

    lines = [f"💰 *Budget — {overview['month']}*\n"]

    for b in overview["budgets"]:
        emoji = CATEGORY_EMOJIS.get(b["category"], "📌")

        # Progress bar
        pct = min(b["percentage"], 100)
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)

        # Status indicator
        if b["percentage"] >= 100:
            status = "🔴"
        elif b["percentage"] >= 80:
            status = "🟡"
        else:
            status = "🟢"

        lines.append(
            f"{emoji} *{b['category']}*\n"
            f"   {status} {bar} {b['percentage']:.0f}%\n"
            f"   {_format_currency(b['spent'])} / {_format_currency(b['budget'])}"
        )

    lines.append(
        f"\n📊 Total Spent: *{_format_currency(overview['total_spent'])}*"
        f"\n📊 Total Budget: *{_format_currency(overview['total_budget'])}*"
    )

    text = "\n".join(lines)
    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=build_budget_menu_keyboard())
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=build_budget_menu_keyboard())


async def _send_recurring_list(message, platform_id: str, edit: bool = False) -> None:
    """Send list of active recurring transactions."""
    data = await _recurring_service.list_recurring(platform_id)

    if "error" in data:
        text = "❌ Kamu belum terdaftar. Kirim /start dulu ya!"
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return

    if data["count"] == 0:
        text = (
            "🔄 *Transaksi Rutin*\n\n"
            "Belum ada transaksi rutin.\n"
            "Tekan *Tambah Rutin* untuk mulai."
        )
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=build_recurring_menu_keyboard())
        else:
            await message.reply_text(text, parse_mode="Markdown", reply_markup=build_recurring_menu_keyboard())
        return

    lines = [f"🔄 *Transaksi Rutin* ({data['count']} aktif)\n"]
    for item in data["items"]:
        emoji = CATEGORY_EMOJIS.get(item["category"], "📌")
        desc = item.get("description") or item.get("merchant") or "-"
        lines.append(
            f"{emoji} *{desc}*\n"
            f"   💰 {_format_currency(item['amount'])} | 📅 {item['frequency']}\n"
            f"   ⏭️ Berikutnya: {item['next_run_date']} | 🆔 #{item['id']}"
        )

    text = "\n".join(lines)

    # For each recurring item, provide action buttons on first item (simplified UX)
    # For full management, show the menu
    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=build_recurring_menu_keyboard())
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=build_recurring_menu_keyboard())
