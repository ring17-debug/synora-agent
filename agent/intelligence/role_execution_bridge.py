"""
Synora Role Execution Bridge V1.

Bridge antara ExecutionEngineV2 dan RoleEngine.

Alur:

    AgentExecutionContext
            ↓
    RoleEngine.run(...)
            ↓
    RoleExecutionResult
            ↓
    structured result
            ↓
    ExecutionEngineV2

Bridge ini sengaja dipisahkan dari kedua engine agar:
- ExecutionEngineV2 tetap generic.
- RoleEngine tetap fokus pada provider/model execution.
- provider credential tidak masuk ke execution state.
- hasil role tetap structured.
"""

from __future__ import annotations

from typing import Any

from .execution_engine_v2 import (
    AgentExecutionContext,
)
from .role_engine import (
    RoleEngine,
)


class RoleExecutionBridge:
    """
    Menghubungkan RoleEngine dengan ExecutionEngineV2.
    """

    def __init__(
        self,
        role_engine: RoleEngine,
    ) -> None:

        if role_engine is None:
            raise ValueError(
                "RoleEngine wajib diberikan."
            )

        self.role_engine = role_engine

    # ========================================================
    # HANDLER FACTORY
    # ========================================================

    def create_handler(
        self,
        role: str,
    ):
        """
        Membuat handler yang kompatibel dengan:

            ExecutionEngineV2.register(role, handler)
        """

        if not isinstance(role, str):
            raise TypeError(
                "role harus string."
            )

        normalized_role = role.strip()

        if not normalized_role:
            raise ValueError(
                "role tidak boleh kosong."
            )

        def handler(
            context: AgentExecutionContext,
        ) -> dict[str, Any]:
            return self.execute(
                role=normalized_role,
                context=context,
            )

        return handler

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(
        self,
        *,
        role: str,
        context: AgentExecutionContext,
    ) -> dict[str, Any]:
        """
        Jalankan RoleEngine menggunakan execution context.
        """

        if not isinstance(
            context,
            AgentExecutionContext,
        ):
            raise TypeError(
                "context harus AgentExecutionContext."
            )

        previous_result = self._build_previous_result(
            context
        )

        result = self.role_engine.run(
            role=role,
            task=context.task,
            context=context.context,
            previous_result=previous_result,
            memory_context="",
        )

        return self._normalize_result(
            result
        )

    # ========================================================
    # PREVIOUS RESULTS
    # ========================================================

    @staticmethod
    def _build_previous_result(
        context: AgentExecutionContext,
    ) -> str:
        """
        Membuat ringkasan hasil role sebelumnya.

        Untuk kompatibilitas RoleEngine V2.2, hasil structured
        dikirim sebagai text JSON-like tanpa credential.
        """

        if not context.previous_results:
            return ""

        sections: list[str] = []

        for result in context.previous_results:

            role = getattr(
                result,
                "role",
                "unknown",
            )

            status = getattr(
                result,
                "status",
                "unknown",
            )

            output = getattr(
                result,
                "output",
                "",
            )

            error = getattr(
                result,
                "error",
                "",
            )

            sections.extend(
                [
                    f"ROLE: {role}",
                    f"STATUS: {status}",
                ]
            )

            if output:
                sections.extend(
                    [
                        "OUTPUT:",
                        str(output),
                    ]
                )

            if error:
                sections.extend(
                    [
                        "ERROR:",
                        str(error),
                    ]
                )

            sections.append("")

        return "\n".join(
            sections
        ).strip()

    # ========================================================
    # RESULT NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize_result(
        result: Any,
    ) -> dict[str, Any]:
        """
        Ubah RoleExecutionResult menjadi contract yang
        dipahami ExecutionEngineV2.
        """

        if result is None:
            return {
                "success": False,
                "status": "failed",
                "output": "",
                "error": (
                    "RoleEngine mengembalikan "
                    "result kosong."
                ),
                "metadata": {},
            }

        if isinstance(
            result,
            dict,
        ):
            normalized = dict(result)

        else:

            to_dict = getattr(
                result,
                "to_dict",
                None,
            )

            if callable(to_dict):

                try:
                    converted = to_dict()

                except Exception as error:
                    return {
                        "success": False,
                        "status": "failed",
                        "output": "",
                        "error": (
                            "Gagal membaca "
                            "RoleExecutionResult."
                        ),
                        "metadata": {
                            "bridge_error":
                                str(error),
                        },
                    }

                if isinstance(
                    converted,
                    dict,
                ):
                    normalized = dict(
                        converted
                    )

                else:
                    normalized = {}

            else:
                normalized = {}

        success = normalized.get(
            "success"
        )

        if not isinstance(
            success,
            bool,
        ):
            success = True

        output = normalized.get(
            "output",
            "",
        )

        if output is None:
            output = ""

        if not isinstance(
            output,
            str,
        ):
            output = str(output)

        error = normalized.get(
            "error"
        )

        if error is not None:
            error = str(error)

        metadata = normalized.get(
            "metadata"
        )

        if not isinstance(
            metadata,
            dict,
        ):
            metadata = {}

        safe_metadata = (
            RoleExecutionBridge._sanitize_metadata(
                metadata
            )
        )

        return {
            "success": success,
            "status": (
                "success"
                if success
                else "failed"
            ),
            "output": output,
            "error": error,
            "metadata": safe_metadata,
        }

    # ========================================================
    # METADATA SANITIZATION
    # ========================================================

    @classmethod
    def _sanitize_metadata(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Recursive metadata sanitization.

        Credential tidak boleh masuk execution state.
        """

        blocked = {
            "api_key",
            "apikey",
            "authorization",
            "password",
            "secret",
            "token",
            "access_token",
            "refresh_token",
            "private_key",
            "credential",
        }

        result: dict[str, Any] = {}

        for key, item in value.items():

            normalized_key = (
                str(key)
                .strip()
                .lower()
            )

            if normalized_key in blocked:
                continue

            if isinstance(
                item,
                dict,
            ):
                result[key] = (
                    cls._sanitize_metadata(
                        item
                    )
                )

            elif isinstance(
                item,
                list,
            ):
                result[key] = [
                    (
                        cls._sanitize_metadata(
                            entry
                        )
                        if isinstance(
                            entry,
                            dict,
                        )
                        else entry
                    )
                    for entry in item
                ]

            else:
                result[key] = item

        return result


__all__ = [
    "RoleExecutionBridge",
]
