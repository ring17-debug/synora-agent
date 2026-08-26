"""
Synora Gemini Provider Adapter V1.

Tujuan:
- menyediakan satu interface Gemini untuk semua agent role
- menggunakan GeminiKeyPool
- mendukung 1 API key sekarang
- siap untuk multi-key di masa depan
- automatic failover jika pool memiliki beberapa key
- tidak membocorkan API key
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from google import genai

from .providers import GeminiKeyPool, ProviderKey


# ============================================================
# CONFIG
# ============================================================

DEFAULT_MODEL = "gemini-3.6-flash"


# ============================================================
# RESPONSE
# ============================================================

@dataclass(frozen=True)
class GeminiResponse:
    """
    Hasil request Gemini yang sudah dinormalisasi.
    """

    text: str
    provider: str
    model: str


# ============================================================
# ADAPTER
# ============================================================

class GeminiAdapter:
    """
    Adapter tunggal untuk komunikasi dengan Gemini.

    Jangan gunakan genai.Client secara langsung dari role.

    Gunakan:

        adapter.generate(...)

    """

    def __init__(
        self,
        key_pool: Optional[GeminiKeyPool] = None,
        model: Optional[str] = None,
    ) -> None:

        self.key_pool = (
            key_pool
            if key_pool is not None
            else GeminiKeyPool.from_environment()
        )

        self.model = (
            model
            or __import__(
                "os"
            ).getenv(
                "GEMINI_MODEL",
                DEFAULT_MODEL,
            )
        )

        if not self.model:
            raise RuntimeError(
                "Gemini model tidak tersedia."
            )

    # --------------------------------------------------------
    # CLIENT
    # --------------------------------------------------------

    @staticmethod
    def _create_client(
        provider: ProviderKey,
    ) -> genai.Client:
        """
        Membuat Gemini client untuk provider tertentu.

        API key tidak pernah dicetak.
        """

        return genai.Client(
            api_key=provider.key,
        )

    # --------------------------------------------------------
    # GENERATE
    # --------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
    ) -> GeminiResponse:
        """
        Mengirim prompt ke Gemini.

        Key pool menentukan provider/key yang digunakan.
        """

        if not isinstance(prompt, str):
            raise TypeError(
                "prompt harus string."
            )

        if not prompt.strip():
            raise ValueError(
                "prompt tidak boleh kosong."
            )

        selected_model = (
            model
            or self.model
        )

        if not selected_model:
            raise RuntimeError(
                "Model Gemini tidak tersedia."
            )

        def operation(
            provider: ProviderKey,
        ) -> GeminiResponse:

            client = self._create_client(
                provider
            )

            response = client.models.generate_content(
                model=selected_model,
                contents=prompt,
            )

            text = getattr(
                response,
                "text",
                None,
            )

            if text is None:
                text = ""

            if not isinstance(
                text,
                str,
            ):
                text = str(text)

            return GeminiResponse(
                text=text,
                provider=provider.name,
                model=selected_model,
            )

        return self.key_pool.execute(
            operation
        )

    # --------------------------------------------------------
    # TEXT HELPER
    # --------------------------------------------------------

    def generate_text(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
    ) -> str:
        """
        Convenience helper jika caller hanya membutuhkan text.
        """

        response = self.generate(
            prompt,
            model=model,
        )

        return response.text

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """
        Status aman adapter.

        Tidak mengembalikan API key.
        """

        return {
            "provider": "gemini",
            "model": self.model,
            "key_pool": self.key_pool.status(),
        }


# ============================================================
# FACTORY
# ============================================================

def create_gemini_adapter(
    *,
    model: Optional[str] = None,
) -> GeminiAdapter:
    """
    Factory sederhana untuk membuat adapter.
    """

    return GeminiAdapter(
        model=model,
    )


__all__ = [
    "GeminiResponse",
    "GeminiAdapter",
    "create_gemini_adapter",
]
