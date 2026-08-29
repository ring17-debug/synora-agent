"""
Synora Role Engine V2.2.

Role definition menggunakan roles.py sebagai single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from .gemini_adapter import GeminiAdapter
from .roles import AgentRole, get_role


# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_role_text(text: Any) -> str:
    """Membersihkan output model tanpa mengubah isi secara agresif."""

    if text is None:
        return ""

    if not isinstance(text, str):
        text = str(text)

    text = text.strip()

    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()

        if len(lines) >= 2:
            if lines[0].strip().startswith("```"):
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
    """Parse hasil role sebagai JSON jika memungkinkan."""

    if isinstance(value, (dict, list)):
        return value

    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    text = value.strip()

    if not text:
        return None

    try:
        parsed = json.loads(text)

        if isinstance(parsed, (dict, list)):
            return parsed

    except json.JSONDecodeError:
        pass

    cleaned = clean_role_text(text)

    try:
        parsed = json.loads(cleaned)

        if isinstance(parsed, (dict, list)):
            return parsed

    except json.JSONDecodeError:
        pass

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
    """Hasil eksekusi satu role."""

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


RoleResult = RoleExecutionResult


# ============================================================
# ENGINE
# ============================================================

class RoleEngine:
    """Menjalankan AgentRole melalui GeminiAdapter."""

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

        if isinstance(role, AgentRole):
            return role.name.strip().lower()

        value = getattr(
            role,
            "value",
            role,
        )

        return str(
            value
        ).strip().lower()

    # ========================================================
    # ROLE RESOLUTION
    # ========================================================

    @staticmethod
    def _resolve_role(
        role: AgentRole | str,
    ) -> AgentRole:

        if isinstance(role, AgentRole):
            return role

        return get_role(
            RoleEngine._role_name(role)
        )

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
        Membuat prompt role.

        Seluruh identity, responsibility, constraint,
        dan output contract berasal dari roles.py.
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

        role_definition = RoleEngine._resolve_role(
            role
        )

        role_name = role_definition.name

        sections = [
            "SYNORA ROLE EXECUTION",
            "",
            f"ROLE: {role_name}",
            "",
            "ROLE DESCRIPTION:",
            role_definition.description.strip(),
            "",
            role_definition.build_instruction(),
            "",
            "TASK:",
            task,
        ]

        if isinstance(context, str) and context.strip():
            sections.extend(
                [
                    "",
                    "PROJECT CONTEXT:",
                    context.strip(),
                ]
            )

        if isinstance(
            previous_result,
            str,
        ) and previous_result.strip():

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
                "- Jangan memperluas scope tanpa alasan.",
                "- Bedakan fakta, asumsi, dan rekomendasi.",
                "- Jangan mengklaim verification yang belum dilakukan.",
                "- Fokus pada tanggung jawab role.",
                "",
                "RETURN:",
                "Berikan hasil kerja yang konkret, terstruktur, "
                "dan dapat diteruskan ke role berikutnya.",
            ]
        )

        return "\n".join(sections)

    # ========================================================
    # GEMINI
    # ========================================================

    def _generate(
        self,
        prompt: str,
    ) -> str:
        """Memanggil GeminiAdapter dengan compatibility API."""

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
                result = method(prompt)

            except TypeError:

                try:
                    result = method(
                        prompt=prompt
                    )

                except TypeError:
                    continue

            if result is None:
                return ""

            if isinstance(result, str):
                return clean_role_text(result)

            text = getattr(
                result,
                "text",
                None,
            )

            if text is not None:
                return clean_role_text(text)

            if isinstance(result, dict):

                for key in (
                    "text",
                    "output",
                    "content",
                    "response",
                ):

                    candidate = result.get(key)

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
        """Menjalankan satu role."""

        try:
            role_definition = self._resolve_role(role)

        except (
            TypeError,
            ValueError,
        ) as error:

            return RoleExecutionResult(
                role=self._role_name(role) or "unknown",
                success=False,
                output="",
                error=str(error),
                metadata={
                    "provider": "gemini",
                    "role_resolved": False,
                },
            )

        role_name = role_definition.name

        if not isinstance(task, str):
            return RoleExecutionResult(
                role=role_name,
                success=False,
                output="",
                error="task harus string.",
                metadata={
                    "provider": "gemini",
                    "role_resolved": True,
                },
            )

        if not task.strip():
            return RoleExecutionResult(
                role=role_name,
                success=False,
                output="",
                error="task tidak boleh kosong.",
                metadata={
                    "provider": "gemini",
                    "role_resolved": True,
                },
            )

        combined_context = (
            context.strip()
            if isinstance(context, str)
            else str(context).strip()
        )

        memory_text = (
            memory_context.strip()
            if isinstance(memory_context, str)
            else str(memory_context).strip()
        )

        if memory_text:

            if combined_context:
                combined_context += (
                    "\n\n"
                    "MEMORY CONTEXT:\n"
                    + memory_text
                )

            else:
                combined_context = (
                    "MEMORY CONTEXT:\n"
                    + memory_text
                )

        try:
            prompt = self.build_prompt(
                role=role_definition,
                task=task,
                context=combined_context,
                previous_result=previous_result,
            )

            output = self._generate(prompt)

            if not output:
                return RoleExecutionResult(
                    role=role_name,
                    success=False,
                    output="",
                    error=(
                        "Gemini mengembalikan "
                        "output kosong."
                    ),
                    metadata={
                        "provider": "gemini",
                        "role_resolved": True,
                        "prompt_generated": True,
                    },
                )

            parsed_json = parse_json_result(output)

            return RoleExecutionResult(
                role=role_name,
                success=True,
                output=output,
                metadata={
                    "provider": "gemini",
                    "role_resolved": True,
                    "prompt_generated": True,
                    "json_detected": (
                        parsed_json is not None
                    ),
                    "role_priority": (
                        role_definition.priority
                    ),
                },
            )

        except Exception as error:

            error_text = str(error)
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
                    "role_resolved": True,
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
