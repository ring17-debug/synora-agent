"""
Synora Agent Runtime V1.

High-level runtime yang menghubungkan:

    Task
      ↓
    Router
      ↓
    Context Engine
      ↓
    Memory Engine
      ↓
    Decision Engine
      ↓
    Execution Engine V2
      ↓
    Role Engine
      ↓
    Gemini Adapter

Runtime dirancang untuk:
- satu Gemini API key dari .env;
- dependency injection untuk testing;
- context project otomatis;
- persistent memory lokal;
- decision-driven execution;
- state-aware multi-agent execution;
- safe status tanpa secret;
- compatibility dengan API runtime sebelumnya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .context_engine import ContextEngine
from .decision_engine import (
    ACTION_ABORT,
    ACTION_CODE,
    ACTION_DEBUG,
    ACTION_FINISH,
    ACTION_PLAN,
    ACTION_REPAIR,
    ACTION_REVIEW,
    ACTION_TEST,
    AgentDecision,
    DecisionEngine,
)
from .execution_engine_v2 import (
    AgentExecutionResult,
    ExecutionEngineV2,
)
from .gemini_adapter import (
    GeminiAdapter,
    create_gemini_adapter,
)
from .memory_engine import MemoryEngine
from .role_engine import RoleEngine
from .router import (
    IntelligenceRouter,
    RoutingDecision,
)


# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_PIPELINE = [
    "planner",
    "coder",
    "reviewer",
    "tester",
]


# ============================================================
# RESULT
# ============================================================

@dataclass
class AgentRuntimeResult:
    """
    Hasil runtime yang aman untuk caller.

    Tidak menyimpan API key.
    """

    task: str
    action: str
    status: str

    role: Optional[str] = None
    confidence: float = 0.0
    output: str = ""
    reason: str = ""

    repair_rounds: int = 0

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "action": self.action,
            "status": self.status,
            "role": self.role,
            "confidence": self.confidence,
            "output": self.output,
            "reason": self.reason,
            "repair_rounds": self.repair_rounds,
            "metadata": dict(self.metadata),
        }


# ============================================================
# RUNTIME
# ============================================================

class AgentRuntime:
    """
    Synora Agent Runtime.

    Runtime menghubungkan seluruh intelligence subsystem.

    Default dependency:

        GeminiAdapter
        IntelligenceRouter
        ContextEngine
        MemoryEngine
        DecisionEngine
        ExecutionEngineV2
        RoleEngine
    """

    def __init__(
        self,
        *,
        adapter: Optional[GeminiAdapter] = None,
        gemini: Optional[GeminiAdapter] = None,
        context: Optional[ContextEngine] = None,
        memory: Optional[MemoryEngine] = None,
        decision: Optional[DecisionEngine] = None,
        execution: Optional[ExecutionEngineV2] = None,
        role_engine: Optional[RoleEngine] = None,
        root: Optional[str | Path] = None,
        memory_file: Optional[str | Path] = None,
        max_repair_rounds: int = 2,
    ) -> None:

        # ----------------------------------------------------
        # ROOT
        # ----------------------------------------------------

        self.root = self._resolve_root(
            root
        )

        # ----------------------------------------------------
        # GEMINI
        # ----------------------------------------------------

        if adapter is not None:
            self.adapter = adapter

        elif gemini is not None:
            self.adapter = gemini

        else:
            self.adapter = create_gemini_adapter()

        # ----------------------------------------------------
        # ROUTER
        # ----------------------------------------------------

        self.router = IntelligenceRouter()

        # ----------------------------------------------------
        # CONTEXT
        # ----------------------------------------------------

        self.context = (
            context
            if context is not None
            else ContextEngine(
                self.root
            )
        )

        # ----------------------------------------------------
        # MEMORY
        # ----------------------------------------------------

        if memory is not None:
            self.memory = memory

        else:
            resolved_memory_file = (
                Path(memory_file)
                .expanduser()
                .resolve()
                if memory_file is not None
                else (
                    self.root
                    / ".synora-agent"
                    / "memory.json"
                )
            )

            resolved_memory_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.memory = MemoryEngine(
                resolved_memory_file
            )

        # ----------------------------------------------------
        # DECISION
        # ----------------------------------------------------

        self.decision = (
            decision
            if decision is not None
            else DecisionEngine(
                max_repair_rounds=max_repair_rounds
            )
        )

        # ----------------------------------------------------
        # EXECUTION
        # ----------------------------------------------------

        self.execution = (
            execution
            if execution is not None
            else ExecutionEngineV2(
                max_repair_rounds=max_repair_rounds
            )
        )

        # ----------------------------------------------------
        # ROLE ENGINE
        # ----------------------------------------------------

        self.role_engine = (
            role_engine
            if role_engine is not None
            else RoleEngine(
                self.adapter
            )
        )

    # ========================================================
    # ROOT
    # ========================================================

    @staticmethod
    def _resolve_root(
        root: Optional[str | Path],
    ) -> Path:
        """
        Resolve project root.

        Prioritas:

        1. explicit root
        2. SYNORA_PROJECT_ROOT
        3. lokasi package Synora
        """

        if root is not None:
            resolved = (
                Path(root)
                .expanduser()
                .resolve()
            )

            if not resolved.exists():
                raise ValueError(
                    f"Project root tidak ditemukan: {resolved}"
                )

            if not resolved.is_dir():
                raise ValueError(
                    f"Project root bukan directory: {resolved}"
                )

            return resolved

        import os

        env_root = os.getenv(
            "SYNORA_PROJECT_ROOT"
        )

        if env_root:
            resolved = (
                Path(env_root)
                .expanduser()
                .resolve()
            )

            if resolved.exists() and resolved.is_dir():
                return resolved

        # agent/intelligence/agent_runtime.py
        #
        # parents[0] = intelligence
        # parents[1] = agent
        # parents[2] = synora
        return (
            Path(__file__)
            .resolve()
            .parents[2]
        )

    # ========================================================
    # ROUTING
    # ========================================================

    def route(
        self,
        task: str,
    ) -> RoutingDecision:
        """
        Route task ke role awal.
        """

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        return self.router.route(
            task
        )

    # ========================================================
    # CONTEXT
    # ========================================================

    def build_context(
        self,
        task: str,
        *,
        role: Optional[str] = None,
        max_files: int = 8,
        max_chars: int = 12000,
    ) -> Any:
        """
        Build project context.

        Mendukung beberapa signature ContextEngine
        agar runtime tetap kompatibel.
        """

        method = getattr(
            self.context,
            "build_context",
            None,
        )

        if method is None:
            return ""

        attempts = (
            {
                "task": task,
                "role": role,
                "max_files": max_files,
                "max_chars": max_chars,
            },
            {
                "task": task,
                "role": role,
            },
            {
                "task": task,
            },
        )

        for kwargs in attempts:
            try:
                return method(**kwargs)
            except TypeError:
                continue

        return method(task)

    # ========================================================
    # MEMORY
    # ========================================================

    def build_memory_context(
        self,
        task: str,
        *,
        max_entries: int = 8,
    ) -> str:
        """
        Build memory context dari persistent memory.
        """

        method = getattr(
            self.memory,
            "build_context",
            None,
        )

        if method is None:
            return ""

        attempts = (
            {
                "query": task,
                "max_entries": max_entries,
            },
            {
                "task": task,
                "max_entries": max_entries,
            },
            {
                "text": task,
                "max_entries": max_entries,
            },
            {
                "query": task,
            },
            {
                "task": task,
            },
        )

        for kwargs in attempts:
            try:
                result = method(
                    **kwargs
                )

                if result is None:
                    return ""

                if isinstance(
                    result,
                    str,
                ):
                    return result

                return str(result)

            except TypeError:
                continue

        try:
            result = method(task)

            if result is None:
                return ""

            return (
                result
                if isinstance(result, str)
                else str(result)
            )

        except TypeError:
            return ""

    # ========================================================
    # DECISION
    # ========================================================

    def decide(
        self,
        *,
        task: str,
        role: Optional[str] = None,
        verification_passed: Optional[bool] = None,
        reviewer_approved: Optional[bool] = None,
        tester_passed: Optional[bool] = None,
        repair_round: int = 0,
        pipeline_complete: bool = False,
    ) -> AgentDecision:
        """
        Meminta DecisionEngine menentukan langkah berikutnya.
        """

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        # ----------------------------------------------------
        # EMPTY
        # ----------------------------------------------------

        if not task.strip():
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=1.0,
                reason="Task kosong.",
            )

        # ----------------------------------------------------
        # REPAIR LIMIT
        # ----------------------------------------------------

        if (
            repair_round
            > self.decision.max_repair_rounds
        ):
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=1.0,
                reason="Batas repair tercapai.",
                metadata={
                    "repair_round": repair_round,
                },
            )

        # ----------------------------------------------------
        # PREFERRED DECISION API
        # ----------------------------------------------------

        methods = (
            "decide",
            "next_decision",
            "evaluate",
        )

        for method_name in methods:
            method = getattr(
                self.decision,
                method_name,
                None,
            )

            if method is None:
                continue

            attempts = (
                {
                    "task": task,
                    "role": role,
                    "verification_passed":
                        verification_passed,
                    "reviewer_approved":
                        reviewer_approved,
                    "tester_passed":
                        tester_passed,
                    "repair_round":
                        repair_round,
                    "pipeline_complete":
                        pipeline_complete,
                },
                {
                    "task": task,
                    "current_role": role,
                    "verification_passed":
                        verification_passed,
                    "reviewer_approved":
                        reviewer_approved,
                    "tester_passed":
                        tester_passed,
                    "repair_round":
                        repair_round,
                    "pipeline_complete":
                        pipeline_complete,
                },
                {
                    "task": task,
                    "role": role,
                },
                {
                    "task": task,
                },
            )

            for kwargs in attempts:
                try:
                    result = method(
                        **kwargs
                    )

                    if isinstance(
                        result,
                        AgentDecision,
                    ):
                        return result

                except TypeError:
                    continue

        # ----------------------------------------------------
        # FALLBACK POLICY
        # ----------------------------------------------------

        if pipeline_complete:
            return AgentDecision(
                action=ACTION_FINISH,
                next_role=None,
                should_continue=False,
                confidence=0.99,
                reason="Pipeline selesai.",
            )

        if verification_passed is False:
            return AgentDecision(
                action=ACTION_DEBUG,
                next_role="debugger",
                should_continue=True,
                confidence=0.99,
                reason="Verification gagal.",
            )

        if reviewer_approved is False:
            return AgentDecision(
                action=ACTION_REPAIR,
                next_role="coder",
                should_continue=True,
                confidence=0.99,
                reason="Reviewer menolak perubahan.",
            )

        if tester_passed is False:
            return AgentDecision(
                action=ACTION_DEBUG,
                next_role="debugger",
                should_continue=True,
                confidence=0.99,
                reason="Tester gagal.",
            )

        routing = self.route(
            task
        )

        role_map = {
            "planner": (
                ACTION_PLAN,
                "planner",
            ),
            "coder": (
                ACTION_CODE,
                "coder",
            ),
            "reviewer": (
                ACTION_REVIEW,
                "reviewer",
            ),
            "tester": (
                ACTION_TEST,
                "tester",
            ),
            "debugger": (
                ACTION_DEBUG,
                "debugger",
            ),
        }

        action, next_role = role_map.get(
            routing.role,
            (
                ACTION_CODE,
                "coder",
            ),
        )

        return AgentDecision(
            action=action,
            next_role=next_role,
            should_continue=True,
            confidence=routing.confidence,
            reason=routing.reason,
        )

    # ========================================================
    # ROLE
    # ========================================================

    def run_role(
        self,
        *,
        role: str,
        task: str,
        context: str = "",
        memory_context: str = "",
        previous_result: str = "",
    ) -> Any:
        """
        Jalankan role melalui RoleEngine.
        """

        method = getattr(
            self.role_engine,
            "run",
            None,
        )

        if method is None:
            raise RuntimeError(
                "RoleEngine tidak memiliki method run()."
            )

        attempts = (
            {
                "role": role,
                "task": task,
                "context": context,
                "memory_context":
                    memory_context,
                "previous_result":
                    previous_result,
            },
            {
                "role": role,
                "task": task,
                "context": context,
                "memory_context":
                    memory_context,
            },
            {
                "role": role,
                "task": task,
                "context": context,
            },
            {
                "role": role,
                "task": task,
            },
        )

        last_error: Optional[Exception] = None

        for kwargs in attempts:
            try:
                return method(
                    **kwargs
                )
            except TypeError as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "RoleEngine gagal dijalankan."
        )

    # ========================================================
    # EXECUTION HANDLER
    # ========================================================

    def _create_execution_handler(
        self,
        *,
        memory_context: str,
    ):
        """
        Membuat handler adapter untuk ExecutionEngineV2.

        ExecutionEngineV2 membutuhkan:

            AgentExecutionContext -> Any

        Sedangkan RoleEngine membutuhkan:

            role
            task
            context
            memory_context
            previous_result

        Method ini menjadi bridge antara keduanya.
        """

        def handler(execution_context: Any) -> dict[str, Any]:
            previous_result = ""

            if execution_context.previous_results:
                previous = (
                    execution_context.previous_results[-1]
                )

                previous_result = (
                    self._extract_output(previous)
                )

            role_result = self.run_role(
                role=execution_context.role,
                task=execution_context.task,
                context=execution_context.context,
                memory_context=memory_context,
                previous_result=previous_result,
            )

            output = self._extract_output(
                role_result
            )

            status = self._extract_status(
                role_result
            )

            success = self._extract_success(
                role_result
            )

            if (
                status not in {
                    "",
                    "success",
                    "completed",
                    "ok",
                }
                or success is False
            ):
                error = self._extract_error(
                    role_result
                )

                raise RuntimeError(
                    error
                    or (
                        f"Role '{execution_context.role}' "
                        "gagal dieksekusi."
                    )
                )

            return {
                "output": output,
                "metadata": {
                    "role": execution_context.role,
                    "runtime": (
                        "synora-agent-runtime-v1"
                    ),
                },
            }

        return handler

    # ========================================================
    # EXECUTE PIPELINE
    # ========================================================

    def _execute_pipeline(
        self,
        *,
        task: str,
        pipeline: list[str],
        context: str,
        memory_context: str,
    ) -> Any:
        """
        Jalankan pipeline melalui ExecutionEngineV2.

        Handler didaftarkan ulang setiap execution agar
        dependency injection tetap aman untuk testing.
        """

        handler = self._create_execution_handler(
            memory_context=memory_context,
        )

        for role in pipeline:
            self.execution.register(
                role,
                handler,
            )

        state = self.execution.execute(
            task,
            list(pipeline),
            context=context,
        )

        return state

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(
        self,
        task: str,
        *,
        context: Optional[str] = None,
        memory_context: Optional[str] = None,
        max_files: int = 8,
        max_chars: int = 12000,
    ) -> AgentRuntimeResult:
        """
        Eksekusi high-level task.

        Flow:

            route
              ↓
            context
              ↓
            memory
              ↓
            decision
              ↓
            execution pipeline
              ↓
            role engine
              ↓
            Gemini

        Pipeline default:

            planner
              ↓
            coder
              ↓
            reviewer
              ↓
            tester
        """

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        # ----------------------------------------------------
        # EMPTY TASK
        # ----------------------------------------------------

        if not task.strip():
            decision = self.decide(
                task=""
            )

            return AgentRuntimeResult(
                task="",
                action=decision.action,
                status="abort",
                role=None,
                confidence=decision.confidence,
                reason=decision.reason,
                metadata={
                    "root": str(self.root),
                    "pipeline": list(
                        DEFAULT_PIPELINE
                    ),
                },
            )

        normalized_task = task.strip()

        # ----------------------------------------------------
        # ROUTE
        # ----------------------------------------------------

        routing = self.route(
            normalized_task
        )

        # ----------------------------------------------------
        # CONTEXT
        # ----------------------------------------------------

        if context is None:
            context_result = self.build_context(
                normalized_task,
                role=routing.role,
                max_files=max_files,
                max_chars=max_chars,
            )

            context_text = self._to_text(
                context_result
            )

        else:
            context_text = context

        # ----------------------------------------------------
        # MEMORY
        # ----------------------------------------------------

        if memory_context is None:
            memory_text = (
                self.build_memory_context(
                    normalized_task
                )
            )

        else:
            memory_text = memory_context

        # ----------------------------------------------------
        # DECISION
        # ----------------------------------------------------

        decision = self.decide(
            task=normalized_task,
            role=routing.role,
        )

        # ----------------------------------------------------
        # TERMINAL DECISION
        # ----------------------------------------------------

        if decision.action in {
            ACTION_ABORT,
            ACTION_FINISH,
        }:
            return AgentRuntimeResult(
                task=normalized_task,
                action=decision.action,
                status=(
                    "abort"
                    if decision.action
                    == ACTION_ABORT
                    else "success"
                ),
                role=decision.next_role,
                confidence=decision.confidence,
                reason=decision.reason,
                metadata={
                    "root": str(self.root),
                    "routing_role":
                        routing.role,
                    "routing_confidence":
                        routing.confidence,
                    "pipeline": list(
                        DEFAULT_PIPELINE
                    ),
                },
            )

        # ----------------------------------------------------
        # INITIAL ROLE
        # ----------------------------------------------------
        #
        # Routing tetap menjadi sumber role utama untuk
        # high-level execution.
        #
        # Contoh:
        #
        #     task -> "buat endpoint RPC baru"
        #
        # Router -> coder
        #
        # Walaupun DecisionEngine dapat mengatakan:
        #
        #     action = plan
        #     next_role = planner
        #
        # role public runtime tetap coder karena task
        # dirouting sebagai coding task.
        #
        role = routing.role

        if not role and decision.next_role:
            role = decision.next_role

        # ----------------------------------------------------
        # PIPELINE
        # ----------------------------------------------------
        #
        # ExecutionEngineV2 sekarang benar-benar digunakan.
        #
        # Ini menghasilkan:
        #
        #     state.results
        #     state.history
        #     state.plan
        #     state.changes
        #     state.verification
        #
        pipeline = list(
            DEFAULT_PIPELINE
        )

        execution_state = self._execute_pipeline(
            task=normalized_task,
            pipeline=pipeline,
            context=context_text,
            memory_context=memory_text,
        )

        # ----------------------------------------------------
        # PUBLIC OUTPUT
        # ----------------------------------------------------

        execution_results = list(
            execution_state.results
        )

        # Cari output dari role yang dirouting.
        #
        # Ini membuat:
        #
        #     result.role == "coder"
        #
        # tetap konsisten dengan router meskipun pipeline
        # berjalan planner -> coder -> reviewer -> tester.
        output = ""

        for execution_result in execution_results:
            if (
                execution_result.role
                == role
            ):
                output = execution_result.output
                break

        # Fallback ke hasil terakhir.
        if not output and execution_results:
            output = execution_results[-1].output

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        status = (
            "success"
            if execution_state.status
            == "success"
            else execution_state.status
        )

        # ----------------------------------------------------
        # EXECUTION METADATA
        # ----------------------------------------------------
        #
        # Penting:
        #
        # execution["results"]
        # harus LIST.
        #
        # Bukan RoleExecutionResult tunggal.
        #
        execution_metadata = (
            execution_state.to_dict()
        )

        return AgentRuntimeResult(
            task=normalized_task,
            action=decision.action,
            status=status,
            role=role,
            confidence=decision.confidence,
            output=output,
            reason=decision.reason,
            repair_rounds=(
                execution_state.repair_round
            ),
            metadata={
                "root": str(self.root),

                "routing_role":
                    routing.role,

                "routing_confidence":
                    routing.confidence,

                "pipeline": list(
                    pipeline
                ),

                "execution_role":
                    role,

                # Full state dari ExecutionEngineV2.
                "execution":
                    execution_metadata,

                # Observability DecisionEngine.
                "decision_action":
                    decision.action,

                "decision_next_role":
                    decision.next_role,
            },
        )

    # ========================================================
    # TEXT
    # ========================================================

    @staticmethod
    def _to_text(
        value: Any,
    ) -> str:
        """
        Normalisasi object menjadi text.
        """

        if value is None:
            return ""

        if isinstance(
            value,
            str,
        ):
            return value

        for attribute in (
            "to_text",
            "text",
            "content",
            "context",
        ):
            value_attribute = getattr(
                value,
                attribute,
                None,
            )

            if callable(
                value_attribute
            ):
                try:
                    result = (
                        value_attribute()
                    )

                    if isinstance(
                        result,
                        str,
                    ):
                        return result

                except Exception:
                    continue

            elif isinstance(
                value_attribute,
                str,
            ):
                return value_attribute

        return str(value)

    # ========================================================
    # OUTPUT EXTRACTION
    # ========================================================

    @staticmethod
    def _extract_output(
        result: Any,
    ) -> str:
        """
        Ambil output dari berbagai result object.

        Mendukung:

        - str
        - AgentExecutionResult
        - RoleExecutionResult
        - object.text
        - object.output
        - object.content
        - object.result
        - dict
        """

        if result is None:
            return ""

        if isinstance(
            result,
            str,
        ):
            return result

        if isinstance(
            result,
            dict,
        ):
            for key in (
                "output",
                "text",
                "content",
                "result",
            ):
                value = result.get(
                    key
                )

                if isinstance(
                    value,
                    str,
                ):
                    return value

        for attribute in (
            "text",
            "output",
            "content",
            "result",
        ):
            value = getattr(
                result,
                attribute,
                None,
            )

            if isinstance(
                value,
                str,
            ):
                return value

        return str(result)

    # ========================================================
    # STATUS EXTRACTION
    # ========================================================

    @staticmethod
    def _extract_status(
        result: Any,
    ) -> str:
        """
        Ambil status result secara aman.
        """

        if result is None:
            return "success"

        if isinstance(
            result,
            dict,
        ):
            status = result.get(
                "status"
            )

            if status is None:
                if result.get(
                    "success"
                ) is False:
                    return "failed"

                return "success"

            return str(
                status
            )

        status = getattr(
            result,
            "status",
            None,
        )

        if status is None:
            success = getattr(
                result,
                "success",
                None,
            )

            if success is False:
                return "failed"

            return "success"

        if isinstance(
            status,
            str,
        ):
            return status

        return str(status)

    # ========================================================
    # SUCCESS EXTRACTION
    # ========================================================

    @staticmethod
    def _extract_success(
        result: Any,
    ) -> Optional[bool]:
        """
        Ambil success flag jika tersedia.

        Return:
            True
            False
            None jika tidak tersedia.
        """

        if result is None:
            return True

        if isinstance(
            result,
            dict,
        ):
            value = result.get(
                "success"
            )

            if isinstance(
                value,
                bool,
            ):
                return value

            return None

        value = getattr(
            result,
            "success",
            None,
        )

        if isinstance(
            value,
            bool,
        ):
            return value

        return None

    # ========================================================
    # ERROR EXTRACTION
    # ========================================================

    @staticmethod
    def _extract_error(
        result: Any,
    ) -> str:
        """
        Ambil error dari result secara aman.
        """

        if result is None:
            return ""

        if isinstance(
            result,
            dict,
        ):
            value = result.get(
                "error"
            )

            if value is None:
                return ""

            return str(value)

        value = getattr(
            result,
            "error",
            None,
        )

        if value is None:
            return ""

        return str(value)

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict[str, Any]:
        """
        Status runtime.

        IMPORTANT:
        Tidak pernah mengembalikan API key.
        """

        adapter_status: dict[str, Any] = {}

        try:
            method = getattr(
                self.adapter,
                "status",
                None,
            )

            if callable(method):
                result = method()

                if isinstance(
                    result,
                    dict,
                ):
                    adapter_status = dict(
                        result
                    )

        except Exception:
            adapter_status = {
                "provider": "gemini",
                "status": "unavailable",
            }

        return {
            "runtime":
                "synora-agent-runtime-v1",

            "root":
                str(self.root),

            "provider":
                adapter_status,

            "router":
                type(
                    self.router
                ).__name__,

            "context_engine":
                type(
                    self.context
                ).__name__,

            "memory_engine":
                type(
                    self.memory
                ).__name__,

            "decision_engine":
                type(
                    self.decision
                ).__name__,

            "execution_engine":
                type(
                    self.execution
                ).__name__,

            "role_engine":
                type(
                    self.role_engine
                ).__name__,
        }


# ============================================================
# FACTORY
# ============================================================

def create_agent_runtime(
    *,
    adapter: Optional[GeminiAdapter] = None,
    gemini: Optional[GeminiAdapter] = None,
    root: Optional[str | Path] = None,
    memory_file: Optional[str | Path] = None,
    max_repair_rounds: int = 2,
) -> AgentRuntime:
    """
    Factory runtime.

    GeminiAdapter akan mengambil:

        GEMINI_API_KEY

    dari environment/.env melalui provider layer.
    """

    return AgentRuntime(
        adapter=adapter,
        gemini=gemini,
        root=root,
        memory_file=memory_file,
        max_repair_rounds=max_repair_rounds,
    )


__all__ = [
    "AgentRuntime",
    "AgentRuntimeResult",
    "create_agent_runtime",
]
