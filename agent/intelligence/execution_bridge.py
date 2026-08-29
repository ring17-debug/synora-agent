"""
Synora Execution Bridge V1.1.

Bridge antara:
    ExecutionEngineV2
        ↓
    RoleEngine
        ↓
    GeminiAdapter

ExecutionEngineV2 tetap provider-agnostic.
RoleEngine menangani role dan provider.
Bridge meneruskan structured result dari RoleEngine.

Tidak menyimpan API key.
"""

from __future__ import annotations

from typing import Any

from .execution_engine_v2 import (
    AgentExecutionContext,
    ExecutionEngineV2,
)
from .role_engine import (
    RoleEngine,
    RoleExecutionResult,
    create_role_engine,
)


class RoleExecutionBridge:
    """
    Menghubungkan ExecutionEngineV2 dengan RoleEngine.

    Contoh:

        bridge = RoleExecutionBridge(role_engine)

        engine.register(
            "planner",
            bridge.handler,
        )
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
    # CONTEXT
    # ========================================================

    @staticmethod
    def _previous_result(
        context: AgentExecutionContext,
    ) -> str:
        """
        Mengambil hasil role sebelumnya.

        Hanya output terakhir yang diteruskan sebagai
        previous_result agar prompt tidak berkembang tanpa batas.
        """

        if not context.previous_results:
            return ""

        previous = context.previous_results[-1]

        output = getattr(
            previous,
            "output",
            "",
        )

        if output is None:
            return ""

        return str(output).strip()

    # ========================================================
    # PROJECT CONTEXT
    # ========================================================

    @staticmethod
    def _build_context(
        context: AgentExecutionContext,
    ) -> str:
        """
        Mengubah execution context menjadi context tekstual
        untuk RoleEngine.
        """

        sections: list[str] = []

        if context.context.strip():
            sections.extend(
                [
                    "EXECUTION CONTEXT:",
                    context.context.strip(),
                ]
            )

        if context.plan.strip():
            sections.extend(
                [
                    "",
                    "CURRENT PLAN:",
                    context.plan.strip(),
                ]
            )

        if context.changes:
            sections.extend(
                [
                    "",
                    "CURRENT CHANGES:",
                    str(
                        list(context.changes)
                    ),
                ]
            )

        if context.verification:
            sections.extend(
                [
                    "",
                    "CURRENT VERIFICATION:",
                    str(
                        context.verification
                    ),
                ]
            )

        if context.repair_round > 0:
            sections.extend(
                [
                    "",
                    "REPAIR ROUND:",
                    str(
                        context.repair_round
                    ),
                ]
            )

        return "\n".join(sections).strip()

    # ========================================================
    # RESULT
    # ========================================================

    @staticmethod
    def _result_to_output(
        result: RoleExecutionResult,
    ) -> dict[str, Any]:
        """
        Mengubah RoleExecutionResult menjadi output terstruktur
        yang dapat diproses ExecutionEngineV2.
        """

        data: dict[str, Any] = {
            "role": result.role,
            "output": result.output,
            "success": result.success,
            "metadata": dict(
                result.metadata
            ),
        }

        if result.error:
            data["error"] = result.error

        if result.structured is not None:
            if isinstance(result.structured, dict):
                data["structured"] = dict(
                    result.structured
                )
            elif isinstance(result.structured, list):
                data["structured"] = list(
                    result.structured
                )

        return data

    # ========================================================
    # HANDLER
    # ========================================================

    def handler(
        self,
        context: AgentExecutionContext,
    ) -> dict[str, Any]:
        """
        Handler generic untuk ExecutionEngineV2.
        """

        result = self.role_engine.run(
            role=context.role,
            task=context.task,
            context=self._build_context(
                context
            ),
            previous_result=self._previous_result(
                context
            ),
        )

        return self._result_to_output(
            result
        )

    # ========================================================
    # REGISTER
    # ========================================================

    def register_roles(
        self,
        engine: ExecutionEngineV2,
        roles: list[str],
    ) -> None:
        """
        Register handler bridge untuk beberapa role.
        """

        if not isinstance(
            engine,
            ExecutionEngineV2,
        ):
            raise TypeError(
                "engine harus ExecutionEngineV2."
            )

        if not isinstance(
            roles,
            list,
        ):
            raise TypeError(
                "roles harus list."
            )

        for role in roles:

            if not isinstance(
                role,
                str,
            ):
                raise TypeError(
                    "Setiap role harus string."
                )

            engine.register(
                role,
                self.handler,
            )


# ============================================================
# FACTORY
# ============================================================

def create_execution_bridge(
    role_engine: RoleEngine | None = None,
) -> RoleExecutionBridge:
    """
    Factory bridge.

    Jika RoleEngine tidak diberikan,
    factory akan membuat RoleEngine default.
    """

    if role_engine is None:
        role_engine = create_role_engine()

    return RoleExecutionBridge(
        role_engine
    )


# ============================================================
# CONVENIENCE
# ============================================================

def register_role_engine(
    engine: ExecutionEngineV2,
    role_engine: RoleEngine,
    roles: list[str],
) -> RoleExecutionBridge:
    """
    Convenience helper untuk menghubungkan RoleEngine
    ke ExecutionEngineV2.
    """

    bridge = RoleExecutionBridge(
        role_engine
    )

    bridge.register_roles(
        engine,
        roles,
    )

    return bridge


__all__ = [
    "RoleExecutionBridge",
    "create_execution_bridge",
    "register_role_engine",
]
