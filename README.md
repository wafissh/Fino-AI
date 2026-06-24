# Fino 🤖💰

Telegram bot pencatat keuangan otomatis dengan AI.

## Fitur

- 💬 **Catat via teks** — Kirim pesan seperti "Makan siang 25000" dan bot akan mencatat otomatis
- 📷 **Catat via foto** — Kirim foto struk/nota dan bot akan membaca dengan OCR + AI
- 📊 **Ringkasan** — Lihat ringkasan pengeluaran per kategori per bulan
- 🤖 **AI Parsing** — AI untuk mengekstrak informasi transaksi secara cerdas
- 🔄 **Multi-platform** — Desain siap untuk Telegram & WhatsApp

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Server | Python + FastAPI |
| Telegram SDK | python-telegram-bot v21+ |
| Database | SQLite (dev) → PostgreSQL (prod) |
| OCR | Google Cloud Vision API |
| AI Parsing | Gemini API (swappable) |

## Quick Start

### 1. Setup

```bash
# Clone & masuk ke folder project
cd "CATAT AI"

# Buat virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Konfigurasi

```bash
# Copy template env
copy .env.example .env

# Edit .env dan isi:
# - TELEGRAM_BOT_TOKEN (dari @BotFather di Telegram)
# - GEMINI_API_KEY (dari Google AI Studio)
```

### 3. Jalankan

```bash
# Development (polling mode)
python -m app.main
```

Bot akan berjalan di polling mode dan FastAPI di `http://localhost:8000`.

## Struktur Project

```
app/
├── main.py           # FastAPI + bot startup
├── config.py         # Environment & settings
├── bot/
│   ├── handlers.py   # Telegram handlers
│   └── keyboards.py  # Inline keyboards
├── db/
│   ├── database.py   # SQLAlchemy engine
│   ├── models.py     # ORM models
│   └── repositories.py  # CRUD operations
├── services/
│   ├── transaction_service.py  # Business logic
│   └── ocr_service.py         # OCR processing
└── ai/
    ├── base.py             # Abstract AI provider
    ├── gemini_provider.py  # Gemini implementation
    └── parser.py           # AI parser (DI)
```

## Kategori Default

| Kategori | Contoh |
|----------|--------|
| 🍔 Makanan & Minuman | Warteg, kopi, delivery |
| 🚗 Transportasi | Grab, Gojek, bensin |
| 🛒 Belanja | Supermarket, marketplace |
| 💡 Tagihan & Utilitas | Listrik, internet, pulsa |
| 🎮 Hiburan | Netflix, bioskop, game |
| 🏥 Kesehatan | Apotek, dokter, gym |
| 📦 Lainnya | Tidak terkategori |

## Mengganti AI Provider

Arsitektur menggunakan abstraction layer sehingga bisa swap AI model tanpa ubah kode:

```python
# Default: Gemini
from app.ai.gemini_provider import GeminiProvider
parser = TransactionParser(provider=GeminiProvider())

# Swap ke provider lain (contoh):
from app.ai.openai_provider import OpenAIProvider
parser = TransactionParser(provider=OpenAIProvider())
```

Cukup implement interface `AIProvider` di `app/ai/base.py`.
