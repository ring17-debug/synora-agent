"""
Synora Agent Orchestrator.

High-level orchestration layer untuk multi-agent execution.

Architecture:

    User Task
        ↓
    IntelligenceRouter
        ↓
    Pipeline Builder
        ↓
    VerifiedExecutionEngine
        ↓
    ExecutionEngineV2
        ↓
    VerificationEngine
        ↓
    Repair Loop
        ↓
    Final ExecutionState

Orchestrator bertanggung jawab untuk:
- memilih primary role melalui router
- membangun pipeline
- menjalankan pipeline
- menjalankan verified execution
- menjalankan verification + repair loop
- menyediakan pipeline description
- menyediakan registrasi handler dan verification
- tidak menyimpan credential/API key
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .execution_engine_v2 import (
    ExecutionEngineV2,
    ExecutionState,
)
from .router import (
    IntelligenceRouter,
    RoutingDecision,
)
from .roles import get_role
from .verified_execution_engine import (
    VerifiedExecutionEngine,
)
from .verification_engine import (
    VerificationEngine,
)


# ============================================================
# AGENT TASK
# ============================================================


@dataclass
class AgentTask:
    """
    Representasi satu task dalam orchestration pipeline.
    """

    task: str
    role: str
    status: str = "pending"
    result: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


# ============================================================
# ORCHESTRATOR
# ============================================================


class AgentOrchestrator:
    """
    High-level orchestrator Synora.

    Pipeline default:

        Planner
          ↓
        Coder
          ↓
        Reviewer
          ↓
        Tester

    Pipeline dapat diarahkan oleh IntelligenceRouter.

    Untuk execution yang membutuhkan safety layer:

        execute_verified()
            ↓
        VerificationEngine

    Untuk execution yang membutuhkan self-repair:

        execute_with_repair()
            ↓
        verification
            ↓
        repairer
            ↓
        re-verification
    """

    def __init__(
        self,
        *,
        router: IntelligenceRouter | None = None,
        execution_engine: ExecutionEngineV2 | None = None,
        verification_engine: VerificationEngine | None = None,
        verified_execution_engine: (
            VerifiedExecutionEngine | None
        ) = None,
    ) -> None:
        """
        Membuat orchestrator.

        Dependency dapat diinjeksi untuk testing maupun
        penggunaan production.
        """

        if router is not None and not isinstance(
            router,
            IntelligenceRouter,
        ):
            raise TypeError(
                "router harus IntelligenceRouter."
            )

        if execution_engine is not None and not isinstance(
            execution_engine,
            ExecutionEngineV2,
        ):
            raise TypeError(
                "execution_engine harus ExecutionEngineV2."
            )

        if verification_engine is not None and not isinstance(
            verification_engine,
            VerificationEngine,
        ):
            raise TypeError(
                "verification_engine harus VerificationEngine."
            )

        if (
            verified_execution_engine is not None
            and not isinstance(
                verified_execution_engine,
                VerifiedExecutionEngine,
            )
        ):
            raise TypeError(
                "verified_execution_engine harus "
                "VerifiedExecutionEngine."
            )

        self.router = (
            router
            if router is not None
            else IntelligenceRouter()
        )

        if verified_execution_engine is not None:
            self.verified_execution_engine = (
                verified_execution_engine
            )

            self.execution_engine = (
                verified_execution_engine.execution_engine
            )

            self.verification_engine = (
                verified_execution_engine.verification_engine
            )

        else:
            self.execution_engine = (
                execution_engine
                if execution_engine is not None
                else ExecutionEngineV2()
            )

            self.verification_engine = (
                verification_engine
                if verification_engine is not None
                else VerificationEngine()
            )

            self.verified_execution_engine = (
                VerifiedExecutionEngine(
                    execution_engine=self.execution_engine,
                    verification_engine=self.verification_engine,
                )
            )

    # ========================================================
    # CLASSIFY
    # ========================================================

    def classify(
        self,
        task: str,
    ) -> RoutingDecision:
        """
        Tentukan primary role berdasarkan task.
        """

        if not isinstance(
            task,
            str,
        ):
            raise TypeError(
                "task harus string."
            )

        return self.router.route(
            task
        )

    # ========================================================
    # BUILD PIPELINE
    # ========================================================

    def build_pipeline(
        self,
        task: str,
        primary_role: str | None = None,
    ) -> list[AgentTask]:
        """
        Bangun pipeline AgentTask.

        Default:

            planner → coder → reviewer → tester

        Debugger:

            debugger → coder → reviewer → tester

        Tester:

            tester → debugger → coder → tester

        Reviewer:

            reviewer → coder → tester
        """

        if not isinstance(
            task,
            str,
        ):
            raise TypeError(
                "task harus string."
            )

        if not task.strip():
            raise ValueError(
                "task tidak boleh kosong."
            )

        if primary_role is None:
            decision = self.classify(
                task
            )
            primary_role = decision.role

        if not isinstance(
            primary_role,
            str,
        ):
            raise TypeError(
                "primary_role harus string."
            )

        primary_role = primary_role.strip()

        if not primary_role:
            raise ValueError(
                "primary_role tidak boleh kosong."
            )

        if primary_role == "debugger":
            roles = [
                "debugger",
                "coder",
                "reviewer",
                "tester",
            ]

        elif primary_role == "tester":
            roles = [
                "tester",
                "debugger",
                "coder",
                "tester",
            ]

        elif primary_role == "reviewer":
            roles = [
                "reviewer",
                "coder",
                "tester",
            ]

        else:
            roles = [
                "planner",
                "coder",
                "reviewer",
                "tester",
            ]

        return [
            AgentTask(
                task=task,
                role=role,
            )
            for role in roles
        ]

    # ========================================================
    # PIPELINE ROLES
    # ========================================================

    def pipeline_roles(
        self,
        task: str,
        primary_role: str | None = None,
    ) -> list[str]:
        """
        Return hanya nama role dari pipeline.

        Berguna ketika pipeline akan diberikan ke
        ExecutionEngineV2 / VerifiedExecutionEngine.
        """

        return [
            item.role
            for item in self.build_pipeline(
                task,
                primary_role=primary_role,
            )
        ]

    # ========================================================
    # DESCRIBE PIPELINE
    # ========================================================

    def describe_pipeline(
        self,
        task: str,
    ) -> list[dict[str, Any]]:
        """
        Menghasilkan metadata pipeline yang aman
        untuk UI, API, logging, atau debugging.
        """

        pipeline = self.build_pipeline(
            task
        )

        result: list[dict[str, Any]] = []

        for index, item in enumerate(
            pipeline,
            start=1,
        ):
            role = get_role(
                item.role
            )

            result.append(
                {
                    "step": index,
                    "role": role.name,
                    "description": role.description,
                    "status": item.status,
                }
            )

        return result

    # ========================================================
    # REGISTER ROLE
    # ========================================================

    def register_role(
        self,
        role: str,
        handler: Any,
    ) -> None:
        """
        Register execution handler untuk role.

        Delegated ke ExecutionEngineV2.
        """

        self.execution_engine.register(
            role,
            handler,
        )

    # ========================================================
    # UNREGISTER ROLE
    # ========================================================

    def unregister_role(
        self,
        role: str,
    ) -> bool:
        """
        Hapus execution handler.
        """

        return self.execution_engine.unregister(
            role
        )

    # ========================================================
    # HAS ROLE
    # ========================================================

    def has_role(
        self,
        role: str,
    ) -> bool:
        """
        Mengecek apakah execution handler tersedia.
        """

        return self.execution_engine.has_handler(
            role
        )

    # ========================================================
    # REGISTER VERIFICATION
    # ========================================================

    def register_verification(
        self,
        name: str,
        check: Any,
    ) -> None:
        """
        Register verification check.
        """

        self.verified_execution_engine.register_verification(
            name,
            check,
        )

    # ========================================================
    # UNREGISTER VERIFICATION
    # ========================================================

    def unregister_verification(
        self,
        name: str,
    ) -> bool:
        """
        Hapus verification check.
        """

        return (
            self.verified_execution_engine
            .unregister_verification(
                name
            )
        )

    # ========================================================
    # HAS VERIFICATION
    # ========================================================

    def has_verification(
        self,
        name: str,
    ) -> bool:
        """
        Mengecek verification check.
        """

        return (
            self.verified_execution_engine
            .has_verification(
                name
            )
        )

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(
        self,
        task: str,
        *,
        primary_role: str | None = None,
        pipeline: list[str] | None = None,
        context: str = "",
        verify: bool = True,
    ) -> ExecutionState:
        """
        Jalankan task melalui orchestration pipeline.

        Jika pipeline tidak diberikan, pipeline akan dibuat
        berdasarkan router / primary_role.

        verify=True:
            execution + verification

        verify=False:
            execution saja.
        """

        if pipeline is None:
            pipeline = self.pipeline_roles(
                task,
                primary_role=primary_role,
            )
        else:
            pipeline = list(
                pipeline
            )

        return self.verified_execution_engine.execute(
            task,
            pipeline,
            context=context,
            verify=verify,
        )

    # ========================================================
    # EXECUTE VERIFIED
    # ========================================================

    def execute_verified(
        self,
        task: str,
        *,
        primary_role: str | None = None,
        pipeline: list[str] | None = None,
        context: str = "",
    ) -> ExecutionState:
        """
        Jalankan task dengan verification wajib.

        Tidak menggunakan repair loop.
        """

        if pipeline is None:
            pipeline = self.pipeline_roles(
                task,
                primary_role=primary_role,
            )
        else:
            pipeline = list(
                pipeline
            )

        return (
            self.verified_execution_engine
            .execute_verified(
                task,
                pipeline,
                context=context,
            )
        )

    # ========================================================
    # EXECUTE WITH REPAIR
    # ========================================================

    def execute_with_repair(
        self,
        task: str,
        *,
        primary_role: str | None = None,
        pipeline: list[str] | None = None,
        context: str = "",
        repair_role: str = "repairer",
        max_repair_rounds: int | None = None,
    ) -> ExecutionState:
        """
        Jalankan:

            pipeline
                ↓
            verification
                ↓
            repair
                ↓
            re-verification

        Jika verification gagal, repairer akan menerima
        verification failure context.

        Repair dibatasi oleh max_repair_rounds.
        """

        if pipeline is None:
            pipeline = self.pipeline_roles(
                task,
                primary_role=primary_role,
            )
        else:
            pipeline = list(
                pipeline
            )

        return (
            self.verified_execution_engine
            .execute_with_repair(
                task,
                pipeline,
                context=context,
                repair_role=repair_role,
                max_repair_rounds=max_repair_rounds,
            )
        )

    # ========================================================
    # ROUTE + EXECUTE
    # ========================================================

    def route_and_execute(
        self,
        task: str,
        *,
        context: str = "",
        verify: bool = True,
    ) -> ExecutionState:
        """
        Convenience method:

            classify
                ↓
            build pipeline
                ↓
            execute
        """

        decision = self.classify(
            task
        )

        return self.execute(
            task,
            primary_role=decision.role,
            context=context,
            verify=verify,
        )

    # ========================================================
    # ROUTE + VERIFIED EXECUTION
    # ========================================================

    def route_and_execute_verified(
        self,
        task: str,
        *,
        context: str = "",
    ) -> ExecutionState:
        """
        Convenience method untuk routed verified execution.
        """

        decision = self.classify(
            task
        )

        return self.execute_verified(
            task,
            primary_role=decision.role,
            context=context,
        )

    # ========================================================
    # ROUTE + REPAIR
    # ========================================================

    def route_and_execute_with_repair(
        self,
        task: str,
        *,
        context: str = "",
        repair_role: str = "repairer",
        max_repair_rounds: int | None = None,
    ) -> ExecutionState:
        """
        Convenience method untuk routed execution dengan
        verification + controlled repair loop.
        """

        decision = self.classify(
            task
        )

        return self.execute_with_repair(
            task,
            primary_role=decision.role,
            context=context,
            repair_role=repair_role,
            max_repair_rounds=max_repair_rounds,
        )
