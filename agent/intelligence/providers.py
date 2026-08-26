"""
Synora Provider / Gemini Key Pool V2.

Features:
- single Gemini API key;
- future multi-key support;
- automatic key selection;
- cooldown;
- success/failure tracking;
- safe status;
- .env loading from project root;
- API key never appears in status/repr/log output.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TypeVar

from dotenv import load_dotenv


T = TypeVar("T")


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT / ".env"


# ============================================================
# PROVIDER KEY
# ============================================================


@dataclass
class ProviderKey:
    """
    Satu credential provider.

    `key` adalah secret dan tidak boleh pernah muncul
    dalam output status atau repr.
    """

    name: str
    key: str

    enabled: bool = True
    success_count: int = 0
    failure_count: int = 0
    cooldown_until: float = 0.0

    def __repr__(self) -> str:
        return (
            "ProviderKey("
            f"name={self.name!r}, "
            "key=<redacted>, "
            f"enabled={self.enabled!r}"
            ")"
        )

    def available(self) -> bool:
        """
        True jika provider aktif dan tidak sedang cooldown.
        """

        if not self.enabled:
            return False

        return time.time() >= self.cooldown_until

    def mark_success(self) -> None:
        """
        Catat request berhasil.
        """

        self.success_count += 1
        self.cooldown_until = 0.0

    def mark_failure(
        self,
        cooldown_seconds: float = 60.0,
    ) -> None:
        """
        Catat request gagal dan aktifkan cooldown.
        """

        self.failure_count += 1

        self.cooldown_until = (
            time.time() + cooldown_seconds
        )

    def safe_status(self) -> dict:
        """
        Status provider tanpa secret.
        """

        remaining = max(
            0.0,
            self.cooldown_until - time.time(),
        )

        return {
            "name": self.name,
            "enabled": self.enabled,
            "available": self.available(),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "cooldown_seconds": round(
                remaining,
                2,
            ),
        }


# ============================================================
# GEMINI KEY POOL
# ============================================================


@dataclass
class GeminiKeyPool:
    """
    Pool Gemini API keys.

    Saat ini cukup menggunakan:

        GEMINI_API_KEY

    Future multi-key:

        GEMINI_API_KEY_1
        GEMINI_API_KEY_2
        GEMINI_API_KEY_3

    Key pool tidak menentukan role.

    Semua role dapat berbagi pool yang sama.
    """

    keys: list[ProviderKey] = field(
        default_factory=list
    )

    cooldown_seconds: float = 60.0

    _cursor: int = field(
        default=0,
        init=False,
        repr=False,
    )

    # ========================================================
    # INIT
    # ========================================================

    def __post_init__(self) -> None:
        if not self.keys:
            raise RuntimeError(
                "Tidak ada Gemini API key yang tersedia. "
                "Set GEMINI_API_KEY di environment atau .env."
            )

        if self.cooldown_seconds < 0:
            raise ValueError(
                "cooldown_seconds harus >= 0."
            )

    # ========================================================
    # COMPATIBILITY
    # ========================================================

    @property
    def providers(self) -> list[ProviderKey]:
        """
        Compatibility alias.

        Beberapa caller lama menggunakan `providers`.
        Internal canonical field tetap `keys`.
        """

        return self.keys

    @property
    def total_keys(self) -> int:
        return len(self.keys)

    @property
    def available_keys(self) -> int:
        return sum(
            1
            for provider in self.keys
            if provider.available()
        )

    # ========================================================
    # ENVIRONMENT
    # ========================================================

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: Optional[Path] = None,
        cooldown_seconds: float = 60.0,
    ) -> "GeminiKeyPool":
        """
        Membuat pool dari environment.

        Urutan:
        1. GEMINI_API_KEY
        2. GEMINI_API_KEY_1
        3. GEMINI_API_KEY_2
        4. ...

        Duplicate key akan diabaikan.

        Secret tidak pernah dicetak.
        """

        selected_env = (
            Path(env_file)
            if env_file is not None
            else DEFAULT_ENV_FILE
        )

        if selected_env.exists():
            load_dotenv(
                selected_env,
                override=False,
            )

        discovered: list[ProviderKey] = []
        seen: set[str] = set()

        # ----------------------------------------------------
        # Primary key
        # ----------------------------------------------------

        primary = os.getenv(
            "GEMINI_API_KEY",
            "",
        ).strip()

        if primary:
            discovered.append(
                ProviderKey(
                    name="gemini-primary",
                    key=primary,
                )
            )

            seen.add(primary)

        # ----------------------------------------------------
        # Numbered keys
        # ----------------------------------------------------

        index = 1

        while True:
            env_name = (
                f"GEMINI_API_KEY_{index}"
            )

            value = os.getenv(
                env_name,
                "",
            ).strip()

            if value:
                if value not in seen:
                    discovered.append(
                        ProviderKey(
                            name=f"gemini-{index}",
                            key=value,
                        )
                    )

                    seen.add(value)

                index += 1
                continue

            # Berhenti setelah menemukan gap.
            #
            # Ini membuat konfigurasi sederhana:
            #
            # GEMINI_API_KEY_1
            # GEMINI_API_KEY_2
            #
            # tetap mudah dipahami.
            break

        return cls(
            keys=discovered,
            cooldown_seconds=cooldown_seconds,
        )

    # ========================================================
    # KEY SELECTION
    # ========================================================

    def select(self) -> ProviderKey:
        """
        Memilih provider yang tersedia.

        Strategi:
        - round-robin;
        - skip disabled;
        - skip cooldown.
        """

        total = len(self.keys)

        if total == 0:
            raise RuntimeError(
                "Gemini key pool kosong."
            )

        for offset in range(total):
            index = (
                self._cursor + offset
            ) % total

            provider = self.keys[index]

            if provider.available():
                self._cursor = (
                    index + 1
                ) % total

                return provider

        raise RuntimeError(
            "Semua Gemini API key sedang "
            "tidak tersedia atau cooldown."
        )

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(
        self,
        operation: Callable[
            [ProviderKey],
            T,
        ],
    ) -> T:
        """
        Jalankan operation menggunakan provider tersedia.

        Jika satu key gagal:
        - failure dicatat;
        - key masuk cooldown;
        - pool mencoba key berikutnya.

        Dengan satu key:
        - request hanya memiliki satu provider;
        - failure diteruskan sebagai error.
        """

        if not callable(operation):
            raise TypeError(
                "operation harus callable."
            )

        attempted: set[str] = set()
        last_error: Optional[
            Exception
        ] = None

        total = len(self.keys)

        for _ in range(total):
            try:
                provider = self.select()

            except RuntimeError as error:
                last_error = error
                break

            if provider.name in attempted:
                break

            attempted.add(
                provider.name
            )

            try:
                result = operation(
                    provider
                )

                provider.mark_success()

                return result

            except Exception as error:
                last_error = error

                provider.mark_failure(
                    self.cooldown_seconds
                )

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Tidak ada Gemini provider yang "
            "berhasil menjalankan operation."
        )

    # ========================================================
    # ENABLE / DISABLE
    # ========================================================

    def disable(
        self,
        name: str,
    ) -> bool:
        """
        Disable provider berdasarkan nama.
        """

        for provider in self.keys:
            if provider.name == name:
                provider.enabled = False
                return True

        return False

    def enable(
        self,
        name: str,
    ) -> bool:
        """
        Enable provider berdasarkan nama.
        """

        for provider in self.keys:
            if provider.name == name:
                provider.enabled = True
                provider.cooldown_until = 0.0
                return True

        return False

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict:
        """
        Safe status.

        Tidak pernah mengembalikan:
        - API key;
        - secret;
        - credential value.
        """

        return {
            "provider": "gemini",
            "total_keys": self.total_keys,
            "available_keys": self.available_keys,
            "cooldown_seconds": self.cooldown_seconds,
            "keys": [
                provider.safe_status()
                for provider in self.keys
            ],
        }


__all__ = [
    "ProviderKey",
    "GeminiKeyPool",
]
