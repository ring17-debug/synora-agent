"""
Synora Gemini Provider Adapter V2.1.

Fungsi:
- satu interface Gemini untuk seluruh role;
- menggunakan GeminiKeyPool;
- mendukung multi-key;
- automatic failover;
- normalisasi response;
- timeout yang dapat dikonfigurasi;
- konfigurasi aman melalui environment;
- API key tidak pernah masuk status/log;
- backward compatible dengan generate_text().
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from google import genai
from google.genai import types

from .providers import GeminiKeyPool, ProviderKey


# ============================================================
# CONFIG
# ============================================================

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT_MS = 120_000


def _env_int(
    name: str,
    default: int,
) -> int:
    """Membaca integer dari environment secara aman."""

    value = os.getenv(name, "").strip()

    if not value:
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


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

    Role tidak boleh membuat genai.Client secara langsung.

    Gunakan:

        adapter.generate(...)

    atau:

        adapter.generate_text(...)
    """

    def __init__(
        self,
        key_pool: Optional[GeminiKeyPool] = None,
        model: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> None:

        self.key_pool = (
            key_pool
            if key_pool is not None
            else GeminiKeyPool.from_environment()
        )

        self.model = (
            model
            or os.getenv(
                "GEMINI_MODEL",
                DEFAULT_MODEL,
            ).strip()
            or DEFAULT_MODEL
        )

        self.timeout_ms = (
            timeout_ms
            if timeout_ms is not None
            else _env_int(
                "GEMINI_TIMEOUT_MS",
                DEFAULT_TIMEOUT_MS,
            )
        )

        if self.timeout_ms <= 0:
            raise ValueError(
                "timeout_ms harus lebih besar dari 0."
            )

        if not self.model:
            raise RuntimeError(
                "Gemini model tidak tersedia."
            )

    # ========================================================
    # CLIENT
    # ========================================================

    def _create_client(
        self,
        provider: ProviderKey,
    ) -> genai.Client:
        """
        Membuat client Gemini untuk provider tertentu.

        Credential hanya digunakan di dalam client.
        Tidak pernah dikembalikan melalui status().
        """

        http_options = types.HttpOptions(
            timeout=self.timeout_ms,
        )

        return genai.Client(
            api_key=provider.key,
            http_options=http_options,
        )

    # ========================================================
    # RESPONSE TEXT
    # ========================================================

    @staticmethod
    def _extract_text(
        response: Any,
    ) -> str:
        """
        Mengambil text dari berbagai bentuk response Gemini.
        """

        if response is None:
            return ""

        text = getattr(
            response,
            "text",
            None,
        )

        if text is not None:
            if isinstance(text, str):
                return text.strip()

            return str(text).strip()

        if isinstance(response, dict):

            for key in (
                "text",
                "output",
                "content",
                "response",
            ):
                value = response.get(key)

                if value is None:
                    continue

                if isinstance(value, str):
                    return value.strip()

                return str(value).strip()

        return str(response).strip()

    # ========================================================
    # GENERATE
    # ========================================================

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
    ) -> GeminiResponse:
        """
        Mengirim prompt ke Gemini.

        GeminiKeyPool menangani:
        - pemilihan key;
        - round-robin;
        - cooldown;
        - failover.
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
            model.strip()
            if isinstance(model, str)
            and model.strip()
            else self.model
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

            response = (
                client.models.generate_content(
                    model=selected_model,
                    contents=prompt,
                )
            )

            text = self._extract_text(
                response
            )

            return GeminiResponse(
                text=text,
                provider=provider.name,
                model=selected_model,
            )

        return self.key_pool.execute(
            operation
        )

    # ========================================================
    # TEXT HELPER
    # ========================================================

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

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict[str, Any]:
        """
        Status aman adapter.

        Tidak mengembalikan:
        - API key;
        - secret;
        - credential value.
        """

        return {
            "provider": "gemini",
            "model": self.model,
            "timeout_ms": self.timeout_ms,
            "key_pool": self.key_pool.status(),
        }


# ============================================================
# FACTORY
# ============================================================

def create_gemini_adapter(
    *,
    model: Optional[str] = None,
    timeout_ms: Optional[int] = None,
) -> GeminiAdapter:
    """
    Factory sederhana untuk membuat adapter.
    """

    return GeminiAdapter(
        model=model,
        timeout_ms=timeout_ms,
    )


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_MS",
    "GeminiResponse",
    "GeminiAdapter",
    "create_gemini_adapter",
]
