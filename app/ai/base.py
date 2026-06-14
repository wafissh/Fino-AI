"""
Abstract base class for AI providers.

To add a new AI provider (e.g., OpenAI, Claude):
1. Create a new file (e.g., openai_provider.py)
2. Implement the AIProvider interface
3. Register it in parser.py

No existing code needs to change — just swap the provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class ParsedTransaction:
    """Structured result from AI parsing of a transaction message or receipt."""

    amount: float
    type: str = "expense"  # 'expense' or 'income'
    category: Optional[str] = None
    merchant: Optional[str] = None
    description: Optional[str] = None
    transaction_date: Optional[date] = None
    confidence: float = 0.0  # 0.0 to 1.0 — how confident the AI is

    def to_dict(self) -> dict:
        return {
            "amount": self.amount,
            "type": self.type,
            "category": self.category,
            "merchant": self.merchant,
            "description": self.description,
            "transaction_date": self.transaction_date.isoformat() if self.transaction_date else None,
            "confidence": self.confidence,
        }


class AIProvider(ABC):
    """
    Abstract interface for AI-powered transaction parsing.

    Implementations must handle:
    - parse_transaction: Parse a single text message into a transaction
    - parse_receipt_text: Parse OCR-extracted text from a receipt (may contain multiple items)
    """

    @abstractmethod
    async def parse_transaction(self, text: str) -> ParsedTransaction:
        """
        Parse a natural language text into a structured transaction.

        Args:
            text: User's message, e.g., "Makan siang di warteg 25000"

        Returns:
            ParsedTransaction with extracted amount, category, etc.
        """
        ...

    @abstractmethod
    async def parse_receipt_text(self, ocr_text: str) -> list[ParsedTransaction]:
        """
        Parse OCR-extracted receipt text into one or more transactions.

        Args:
            ocr_text: Raw text from OCR processing of a receipt photo.

        Returns:
            List of ParsedTransaction objects (receipts may have multiple items).
        """
        ...

    async def parse_receipt_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> list[ParsedTransaction]:
        """
        Parse a receipt image directly into structured transactions.

        Args:
            image_bytes: Raw bytes of the receipt photo.
            mime_type: MIME type of the image, e.g., 'image/jpeg'

        Returns:
            List of ParsedTransaction objects.
        """
        raise NotImplementedError("Direct image parsing is not supported by this provider")

    async def chat_response(self, text: str, user_name: str | None = None) -> str:
        """
        Generate a casual conversational reply when the user's message
        is not a financial transaction (e.g., greetings, questions about the bot).

        Providers may override this to use the AI model for natural replies.
        Default: returns a friendly fallback message.

        Args:
            text: The user's message.
            user_name: Optional display name of the user.

        Returns:
            A string reply to send back to the user.
        """
        name = user_name or "kamu"
        return (
            f"Halo {name}! 👋 Ada yang bisa Jarfin bantu?\n\n"
            "💬 Kamu bisa:\n"
            "• Catat transaksi — _Kopi 15000_\n"
            "• Kirim foto struk\n"
            "• Ketik /ringkasan, /riwayat, /budget\n"
            "• Atau tanya apapun seputar keuanganmu 😊"
        )

