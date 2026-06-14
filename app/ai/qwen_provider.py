"""
Qwen AI provider implementation.

Uses Qwen's OpenAI-compatible Chat Completions API via Alibaba Cloud
DashScope/Maas platform to parse transaction text and receipt OCR output.
"""

import json
import logging
from datetime import date, datetime
from typing import Any
import httpx

from app.ai.base import AIProvider, ParsedTransaction
from app.config import CATEGORY_NAMES, get_settings
from app.ai.gemini_provider import TRANSACTION_PARSE_PROMPT, RECEIPT_PARSE_PROMPT

logger = logging.getLogger(__name__)

# ── Chat System Prompt (persona Jarfin) ─────────────────────────────────────

CHAT_SYSTEM_PROMPT = """Kamu adalah Jarfin, asisten keuangan pribadi yang ramah dan ceria di Telegram.

Kepribadianmu:
- Ramah, santai, dan menggunakan bahasa sehari-hari Indonesia (boleh campur sedikit gaul)
- Singkat dan padat — hindari jawaban terlalu panjang
- Selalu semangat membantu soal keuangan pribadi

Kemampuanmu:
- Mencatat transaksi keuangan (pengeluaran & pemasukan) via teks atau foto struk
- Memberikan ringkasan keuangan bulanan
- Mengelola budget per kategori
- Menjawab pertanyaan umum seputar keuangan pribadi

Kategori yang kamu kenali: Makanan & Minuman, Transportasi, Belanja, Tagihan & Utilitas, Hiburan, Kesehatan, Lainnya.

Cara pakai bot:
- Kirim teks transaksi → contoh: "Kopi Starbucks 55000" atau "Gaji masuk 5jt"
- Kirim foto struk/nota → aku baca otomatis
- /ringkasan → lihat pengeluaran bulan ini
- /riwayat → riwayat transaksi
- /budget → kelola budget
- /rutin → transaksi rutin bulanan
- /hapus <id> → hapus transaksi

Jika pengguna bertanya tentang hal di luar keuangan pribadi, arahkan dengan sopan ke topik keuangan atau jawab singkat saja."""


class QwenProvider(AIProvider):
    """AI provider using Qwen OpenAI-compatible API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.dashscope_api_key
        self.base_url = base_url or settings.qwen_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model = model or settings.qwen_model or "qwen3.5-plus"

        # Ensure base_url does not end with /
        if self.base_url.endswith("/"):
            self.base_url = self.base_url[:-1]

    async def parse_transaction(self, text: str) -> ParsedTransaction:
        """Parse a text message into a structured transaction using Qwen."""
        prompt = TRANSACTION_PARSE_PROMPT.format(
            categories=", ".join(CATEGORY_NAMES),
            text=text,
            today=date.today().isoformat(),
        )

        try:
            if not self.api_key:
                raise ValueError("DashScope/Qwen API key (DASHSCOPE_API_KEY) is not set")

            url = f"{self.base_url}/chat/completions"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                    },
                    timeout=30.0,
                )
                if response.status_code != 200:
                    logger.error(f"Qwen API error response: {response.text}")
                response.raise_for_status()
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                return self._parse_json_response(content)

        except Exception as e:
            logger.error(f"Qwen parse_transaction failed: {e}")
            return self._fallback_parse(text)

    async def parse_receipt_text(self, ocr_text: str) -> list[ParsedTransaction]:
        """Parse OCR receipt text into transactions using Qwen."""
        prompt = RECEIPT_PARSE_PROMPT.format(
            categories=", ".join(CATEGORY_NAMES),
            ocr_text=ocr_text,
            today=date.today().isoformat(),
        )

        try:
            if not self.api_key:
                raise ValueError("DashScope/Qwen API key (DASHSCOPE_API_KEY) is not set")

            url = f"{self.base_url}/chat/completions"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                    },
                    timeout=30.0,
                )
                if response.status_code != 200:
                    logger.error(f"Qwen API error response: {response.text}")
                response.raise_for_status()
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                return self._parse_json_list_response(content)

        except Exception as e:
            logger.error(f"Qwen parse_receipt_text failed: {e}")
            return []

    async def parse_receipt_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> list[ParsedTransaction]:
        """Parse a receipt image directly into structured transactions using Qwen Multimodal."""
        import base64

        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        prompt = RECEIPT_PARSE_PROMPT.format(
            categories=", ".join(CATEGORY_NAMES),
            ocr_text="[Gambar Struk Terlampir]",
            today=date.today().isoformat(),
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}"
                        }
                    }
                ]
            }
        ]

        try:
            if not self.api_key:
                raise ValueError("DashScope/Qwen API key (DASHSCOPE_API_KEY) is not set")

            url = f"{self.base_url}/chat/completions"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                    },
                    timeout=45.0,
                )
                if response.status_code != 200:
                    logger.error(f"Qwen API error response: {response.text}")
                response.raise_for_status()
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                return self._parse_json_list_response(content)

        except Exception as e:
            logger.error(f"Qwen parse_receipt_image failed: {e}")
            return []

    # ── Internal Helpers ────────────────────────────────────────────────

    def _parse_json_response(self, raw: str) -> ParsedTransaction:
        """Parse a single JSON object from response."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        return self._dict_to_parsed(data)

    def _parse_json_list_response(self, raw: str) -> list[ParsedTransaction]:
        """Parse a JSON array from response."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        if isinstance(data, list):
            return [self._dict_to_parsed(item) for item in data]
        return [self._dict_to_parsed(data)]

    def _dict_to_parsed(self, data: dict[str, Any]) -> ParsedTransaction:
        """Convert a dict to ParsedTransaction."""
        txn_date = None
        if data.get("transaction_date"):
            try:
                txn_date = datetime.strptime(data["transaction_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                txn_date = date.today()

        return ParsedTransaction(
            amount=float(data.get("amount", 0)),
            type=data.get("type", "expense"),
            category=data.get("category", "Lainnya"),
            merchant=data.get("merchant"),
            description=data.get("description"),
            transaction_date=txn_date or date.today(),
            confidence=float(data.get("confidence", 0.5)),
        )

    def _fallback_parse(self, text: str) -> ParsedTransaction:
        """Simple fallback when AI fails: extract numbers from text."""
        import re

        numbers = re.findall(r"[\d.,]+", text.replace(".", "").replace(",", ""))
        amount = 0.0
        for num in numbers:
            try:
                val = float(num)
                if val > amount:
                    amount = val
            except ValueError:
                continue

        type_val = "expense"
        lower_text = text.lower()
        if any(w in lower_text for w in ["gaji", "pemasukan", "dapat", "terima", "transfer masuk", "income", "payroll", "bonus", "kembalian"]):
            type_val = "income"

        return ParsedTransaction(
            amount=amount,
            type=type_val,
            category="Lainnya",
            description=text[:100],
            transaction_date=date.today(),
            confidence=0.1,
        )

    async def chat_response(self, text: str, user_name: str | None = None) -> str:
        """
        Generate a natural conversational reply as Jarfin using Qwen API.
        Used when user's message is not a financial transaction.
        """
        user_context = f"(Nama pengguna: {user_name})" if user_name else ""
        user_message = f"{user_context}\n{text}".strip() if user_context else text

        try:
            if not self.api_key:
                raise ValueError("API key not set")

            url = f"{self.base_url}/chat/completions"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.8,
                        "max_tokens": 300,
                    },
                    timeout=20.0,
                )
                response.raise_for_status()
                res_data = response.json()
                reply = res_data["choices"][0]["message"]["content"].strip()
                return reply

        except Exception as e:
            logger.error(f"Qwen chat_response failed: {e}")
            name = user_name or "kamu"
            return (
                f"Halo {name}! 👋 Ada yang bisa Jarfin bantu?\n\n"
                "💬 Mau catat transaksi? Ketik aja langsung, contoh:\n"
                "_Kopi 15000_ atau _Makan siang 25000_"
            )

