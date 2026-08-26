"""
Synora Role Engine V2.

Tugas:
- menjalankan role agent melalui GeminiAdapter;
- membuat prompt berdasarkan role;
- membersihkan output model;
- melakukan parsing JSON result secara aman;
- meneruskan context dan hasil role sebelumnya;
- menjaga compatibility dengan public API Synora.

Credential/API key tidak dikelola di file ini.
Semua komunikasi provider dilakukan melalui GeminiAdapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from .gemini_adapter import GeminiAdapter
from .roles import AgentRole


# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_role_text(
    text: Any,
) -> str:
    """
    Membersihkan output Gemini sebelum diteruskan
    ke pipeline agent.

    Fungsi ini sengaja tidak melakukan perubahan agresif
    terhadap isi kode.
    """

    if text is None:
        return ""

    if not isinstance(text, str):
        text = str(text)

    text = text.strip()

    # Hapus markdown fence di awal/akhir jika seluruh
    # response dibungkus code fence.
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()

        if len(lines) >= 2:
            first = lines[0].strip()

            if first.startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            text = "\n".join(lines).strip()

    return text


# ============================================================
# JSON PARSER
# ============================================================

def parse_json_result(
    value: Any,
) -> dict[str, Any] | list[Any] | None:
    """
    Parse JSON dari hasil role.

    Mendukung:
    - dictionary/list langsung;
    - JSON string;
    - JSON yang dibungkus markdown code fence;
    - JSON yang memiliki sedikit text di sekelilingnya.

    Jika tidak dapat diparse, return None.
    """

    if isinstance(value, (dict, list)):
        return value

    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    text = value.strip()

    if not text:
        return None

    # Percobaan pertama: JSON langsung.
    try:
        parsed = json.loads(text)

        if isinstance(parsed, (dict, list)):
            return parsed

    except json.JSONDecodeError:
        pass

    # Bersihkan markdown fence.
    cleaned = clean_role_text(text)

    try:
        parsed = json.loads(cleaned)

        if isinstance(parsed, (dict, list)):
            return parsed

    except json.JSONDecodeError:
        pass

    # Cari object JSON.
    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")

    if object_start >= 0 and object_end > object_start:

        candidate = cleaned[
            object_start : object_end + 1
        ]

        try:
            parsed = json.loads(candidate)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    # Cari array JSON.
    array_start = cleaned.find("[")
    array_end = cleaned.rfind("]")

    if array_start >= 0 and array_end > array_start:

        candidate = cleaned[
            array_start : array_end + 1
        ]

        try:
            parsed = json.loads(candidate)

            if isinstance(parsed, list):
                return parsed

        except json.JSONDecodeError:
            pass

    return None


# ============================================================
# RESULT
# ============================================================

@dataclass(frozen=True)
class RoleExecutionResult:
    """
    Hasil eksekusi satu role.
    """

    role: str
    success: bool
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


# Compatibility dengan API lama.
RoleResult = RoleExecutionResult


# ============================================================
# ENGINE
# ============================================================

class RoleEngine:
    """
    Menjalankan satu AgentRole melalui GeminiAdapter.
    """

    def __init__(
        self,
        adapter: GeminiAdapter,
    ) -> None:

        if adapter is None:
            raise ValueError(
                "GeminiAdapter wajib diberikan."
            )

        self.adapter = adapter

    # ========================================================
    # ROLE NORMALIZATION
    # ========================================================

    @staticmethod
    def _role_name(
        role: AgentRole | str,
    ) -> str:

        value = getattr(
            role,
            "value",
            role,
        )

        return str(
            value
        ).strip().lower()

    # ========================================================
    # PROMPT
    # ========================================================

    @staticmethod
    def build_prompt(
        role: AgentRole | str,
        task: str,
        context: str = "",
        previous_result: str = "",
    ) -> str:
        """
        Membuat prompt terstruktur untuk role.
        """

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        task = task.strip()

        if not task:
            raise ValueError(
                "task tidak boleh kosong."
            )

        role_name = RoleEngine._role_name(
            role
        )

        role_instructions = {
            "planner": (
                "Analisis task dan buat rencana implementasi "
                "yang konkret. Identifikasi file yang relevan, "
                "perubahan yang diperlukan, risiko, dan urutan kerja."
            ),
            "coder": (
                "Implementasikan perubahan kode yang diperlukan. "
                "Gunakan source code dan context yang tersedia. "
                "Jangan mengarang API atau file yang tidak ada."
            ),
            "reviewer": (
                "Review hasil pekerjaan secara kritis. "
                "Cari bug, regression, security issue, "
                "incorrect assumption, dan masalah correctness."
            ),
            "tester": (
                "Validasi hasil implementasi. "
                "Tentukan test yang relevan dan evaluasi "
                "apakah perubahan benar-benar bekerja."
            ),
            "debugger": (
                "Analisis kegagalan atau error secara sistematis. "
                "Temukan root cause dan tentukan perbaikan yang tepat."
            ),
        }

        instruction = role_instructions.get(
            role_name,
            (
                "Kerjakan task sesuai tanggung jawab role "
                "secara aman dan akurat."
            ),
        )

        sections = [
            "SYNORA ROLE EXECUTION",
            "",
            f"ROLE: {role_name}",
            "",
            "ROLE INSTRUCTION:",
            instruction,
            "",
            "TASK:",
            task,
        ]

        if context.strip():
            sections.extend(
                [
                    "",
                    "PROJECT CONTEXT:",
                    context.strip(),
                ]
            )

        if previous_result.strip():
            sections.extend(
                [
                    "",
                    "PREVIOUS ROLE RESULT:",
                    previous_result.strip(),
                ]
            )

        sections.extend(
            [
                "",
                "EXECUTION RULES:",
                "- Gunakan hanya informasi yang tersedia.",
                "- Jangan mengarang file atau API.",
                "- Jika informasi tidak cukup, nyatakan dengan jelas.",
                "- Jangan menampilkan API key atau secret.",
                "- Fokus pada task yang diberikan.",
                "",
                "RETURN:",
                "Berikan hasil kerja yang konkret dan dapat "
                "diteruskan ke role berikutnya.",
            ]
        )

        return "\n".join(sections)

    # ========================================================
    # GEMINI ADAPTER COMPATIBILITY
    # ========================================================

    def _generate(
        self,
        prompt: str,
    ) -> str:
        """
        Memanggil GeminiAdapter menggunakan method API
        yang tersedia.
        """

        candidates = (
            "generate",
            "generate_text",
            "complete",
            "chat",
            "ask",
        )

        for method_name in candidates:

            method = getattr(
                self.adapter,
                method_name,
                None,
            )

            if not callable(method):
                continue

            try:
                result = method(
                    prompt
                )

            except TypeError:

                try:
                    result = method(
                        prompt=prompt
                    )

                except TypeError:
                    continue

            if result is None:
                return ""

            if isinstance(
                result,
                str,
            ):
                return clean_role_text(
                    result
                )

            if hasattr(
                result,
                "text",
            ):

                text = getattr(
                    result,
                    "text",
                )

                if text is not None:
                    return clean_role_text(
                        text
                    )

            if isinstance(
                result,
                dict,
            ):

                for key in (
                    "text",
                    "output",
                    "content",
                    "response",
                ):

                    candidate = result.get(
                        key
                    )

                    if candidate is not None:
                        return clean_role_text(
                            candidate
                        )

            return clean_role_text(
                str(result)
            )

        raise RuntimeError(
            "GeminiAdapter tidak memiliki "
            "method generate yang kompatibel."
        )

    # ========================================================
    # RUN
    # ========================================================

    def run(
        self,
        *,
        role: AgentRole | str,
        task: str,
        context: str = "",
        previous_result: str = "",
        memory_context: str = "",
    ) -> RoleExecutionResult:
        """
        Menjalankan satu role.

        memory_context didukung sebagai alias tambahan
        untuk compatibility dengan AgentRuntime.
        """

        role_name = self._role_name(
            role
        )

        if not role_name:
            return RoleExecutionResult(
                role="unknown",
                success=False,
                output="",
                error="Role tidak valid.",
            )

        # Gabungkan context biasa dengan memory context.
        combined_context = context.strip()

        if memory_context.strip():

            if combined_context:
                combined_context += (
                    "\n\n"
                    "MEMORY CONTEXT:\n"
                    + memory_context.strip()
                )

            else:
                combined_context = (
                    "MEMORY CONTEXT:\n"
                    + memory_context.strip()
                )

        try:

            prompt = self.build_prompt(
                role=role,
                task=task,
                context=combined_context,
                previous_result=previous_result,
            )

            output = self._generate(
                prompt
            )

            if not output:
                return RoleExecutionResult(
                    role=role_name,
                    success=False,
                    output="",
                    error=(
                        "Gemini mengembalikan "
                        "output kosong."
                    ),
                )

            return RoleExecutionResult(
                role=role_name,
                success=True,
                output=output,
                metadata={
                    "provider": "gemini",
                    "prompt_generated": True,
                    "json_detected": (
                        parse_json_result(output)
                        is not None
                    ),
                },
            )

        except Exception as error:

            error_text = str(
                error
            )

            lowered = error_text.lower()

            sensitive_markers = (
                "api_key",
                "apikey",
                "authorization",
                "bearer",
                "secret",
                "password",
                "token",
            )

            if any(
                marker in lowered
                for marker in sensitive_markers
            ):
                error_text = (
                    "Gemini provider mengalami "
                    "authentication/provider error."
                )

            return RoleExecutionResult(
                role=role_name,
                success=False,
                output="",
                error=error_text,
                metadata={
                    "provider": "gemini",
                },
            )


# ============================================================
# FACTORY
# ============================================================

def create_role_engine(
    adapter: GeminiAdapter | None = None,
) -> RoleEngine:

    if adapter is None:

        from .gemini_adapter import (
            create_gemini_adapter,
        )

        adapter = create_gemini_adapter()

    return RoleEngine(
        adapter=adapter
    )


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "RoleExecutionResult",
    "RoleResult",
    "RoleEngine",
    "clean_role_text",
    "parse_json_result",
    "create_role_engine",
]
