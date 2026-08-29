"""
Synora Intelligence Pipeline V1.

High-level orchestration layer.

Architecture:

    task
      ↓
    ExecutionEngineV2
      ↓
    RoleExecutionBridge
      ↓
    RoleEngine
      ↓
    GeminiAdapter
      ↓
    GeminiKeyPool
      ↓
    Gemini

Pipeline utama:

    planner → coder → reviewer → tester

Repair:

    debugger → coder → reviewer → tester

Layer ini tidak menangani API key secara langsung.
"""

from __future__ import annotations

from typing import Any

from .execution_bridge import (
    RoleExecutionBridge,
)
from .execution_engine_v2 import (
    ExecutionEngineV2,
    ExecutionState,
)
from .role_engine import (
    RoleEngine,
    create_role_engine,
)


DEFAULT_PIPELINE = [
    "planner",
    "coder",
    "reviewer",
    "tester",
]


class SynoraPipeline:
    """
    High-level orchestrator Synora.

    Pipeline bertanggung jawab untuk menghubungkan
    ExecutionEngineV2 dengan RoleEngine.

    Tidak berisi logic provider.
    """

    def __init__(
        self,
        *,
        execution_engine: ExecutionEngineV2 | None = None,
        role_engine: RoleEngine | None = None,
        max_repair_rounds: int = 2,
    ) -> None:

        self.execution_engine = (
            execution_engine
            if execution_engine is not None
            else ExecutionEngineV2(
                max_repair_rounds=max_repair_rounds,
            )
        )

        self.role_engine = (
            role_engine
            if role_engine is not None
            else create_role_engine()
        )

        self.bridge = RoleExecutionBridge(
            self.role_engine
        )

        self.bridge.register_roles(
            self.execution_engine,
            [
                "planner",
                "coder",
                "reviewer",
                "tester",
                "debugger",
            ],
        )

    # ========================================================
    # EXECUTE
    # ========================================================

    def run(
        self,
        task: str,
        *,
        pipeline: list[str] | None = None,
        context: str = "",
    ) -> ExecutionState:
        """
        Menjalankan pipeline utama.
        """

        selected_pipeline = (
            list(pipeline)
            if pipeline is not None
            else list(DEFAULT_PIPELINE)
        )

        return self.execution_engine.execute(
            task,
            selected_pipeline,
            context=context,
        )

    # ========================================================
    # REPAIR
    # ========================================================

    def repair(
        self,
        state: ExecutionState,
        *,
        debugger_role: str = "debugger",
        coder_role: str = "coder",
        reviewer_role: str = "reviewer",
        tester_role: str = "tester",
    ) -> ExecutionState:
        """
        Menjalankan repair cycle.
        """

        return self.execution_engine.execute_repair(
            state,
            debugger_role=debugger_role,
            coder_role=coder_role,
            reviewer_role=reviewer_role,
            tester_role=tester_role,
        )

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict[str, Any]:
        """
        Status aman pipeline.
        """

        return {
            "pipeline": "synora_intelligence",
            "default_roles": list(
                DEFAULT_PIPELINE
            ),
            "execution_engine": (
                self.execution_engine.status()
            ),
        }


# ============================================================
# FACTORY
# ============================================================

def create_pipeline(
    *,
    execution_engine: ExecutionEngineV2 | None = None,
    role_engine: RoleEngine | None = None,
    max_repair_rounds: int = 2,
) -> SynoraPipeline:
    """
    Factory untuk membuat SynoraPipeline.
    """

    return SynoraPipeline(
        execution_engine=execution_engine,
        role_engine=role_engine,
        max_repair_rounds=max_repair_rounds,
    )


__all__ = [
    "DEFAULT_PIPELINE",
    "SynoraPipeline",
    "create_pipeline",
]
