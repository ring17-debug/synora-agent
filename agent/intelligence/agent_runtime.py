"""
Synora Agent Runtime V1.1.

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

Runtime bertanggung jawab terhadap orchestration level tinggi.

Design goals:
- compatibility dengan runtime API sebelumnya;
- dependency injection untuk testing;
- project context otomatis;
- persistent memory lokal;
- decision-driven execution;
- state-aware execution;
- safe status tanpa secret;
- pipeline planner -> coder -> reviewer -> tester;
- repair/debug flow ditangani oleh ExecutionEngineV2;
- tidak menyimpan API key di result;
- tidak menganggap output LLM sebagai verification;
- tidak mengarang execution result.
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

DEFAULT_PIPELINE: tuple[str, ...] = (
    "planner",
    "coder",
    "reviewer",
    "tester",
)

RUNTIME_NAME = "synora-agent-runtime-v1.1"


# ============================================================
# RESULT
# ============================================================

@dataclass
class AgentRuntimeResult:
    """
    Hasil high-level execution.

    Object ini merupakan public result untuk caller.

    Tidak boleh menyimpan:
    - API key
    - Authorization header
    - password
    - credential
    - secret provider
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
        """
        Mengubah result menjadi dictionary.

        Metadata disalin agar caller tidak mendapatkan
        reference mutable internal.
        """

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

    Runtime menghubungkan seluruh intelligence subsystem:

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

        if context is not None:
            self.context = context

        else:
            self.context = ContextEngine(
                self.root
            )

        # ----------------------------------------------------
        # MEMORY
        # ----------------------------------------------------

        if memory is not None:
            self.memory = memory

        else:
            if memory_file is not None:
                resolved_memory_file = (
                    Path(memory_file)
                    .expanduser()
                    .resolve()
                )

            else:
                resolved_memory_file = (
                    self.root
                    / ".synora-agent"
                    / "memory.json"
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

        if decision is not None:
            self.decision = decision

        else:
            self.decision = DecisionEngine(
                max_repair_rounds=max_repair_rounds
            )

        # ----------------------------------------------------
        # EXECUTION
        # ----------------------------------------------------

        if execution is not None:
            self.execution = execution

        else:
            self.execution = ExecutionEngineV2(
                max_repair_rounds=max_repair_rounds
            )

        # ----------------------------------------------------
        # ROLE ENGINE
        # ----------------------------------------------------

        if role_engine is not None:
            self.role_engine = role_engine

        else:
            self.role_engine = RoleEngine(
                self.adapter
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
        3. package location
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

        normalized_task = task.strip()

        if not normalized_task:
            raise ValueError(
                "task tidak boleh kosong."
            )

        return self.router.route(
            normalized_task
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

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        method = getattr(
            self.context,
            "build_context",
            None,
        )

        if not callable(method):
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
                return method(
                    **kwargs
                )

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

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        method = getattr(
            self.memory,
            "build_context",
            None,
        )

        if not callable(method):
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

                return self._to_text(
                    result
                )

            except TypeError:
                continue

        try:

            result = method(
                task
            )

            return self._to_text(
                result
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

        if not task.strip():
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=1.0,
                reason="Task kosong.",
            )

        try:
            normalized_repair_round = int(
                repair_round
            )

        except (
            TypeError,
            ValueError,
        ):
            normalized_repair_round = 0

        if normalized_repair_round < 0:
            normalized_repair_round = 0

        # ----------------------------------------------------
        # REPAIR LIMIT
        # ----------------------------------------------------

        max_repair_rounds = getattr(
            self.decision,
            "max_repair_rounds",
            2,
        )

        try:
            max_repair_rounds = int(
                max_repair_rounds
            )

        except (
            TypeError,
            ValueError,
        ):
            max_repair_rounds = 2

        if normalized_repair_round > max_repair_rounds:
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=1.0,
                reason="Batas repair tercapai.",
                metadata={
                    "repair_round":
                        normalized_repair_round,
                    "max_repair_rounds":
                        max_repair_rounds,
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

            if not callable(method):
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
                        normalized_repair_round,
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
                        normalized_repair_round,
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

                    # Compatibility dengan object
                    # yang mempunyai action/next_role.
                    if (
                        result is not None
                        and hasattr(
                            result,
                            "action",
                        )
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

        # ----------------------------------------------------
        # ROUTER FALLBACK
        # ----------------------------------------------------

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
                ACTION_PLAN,
                "planner",
            ),
        )

        return AgentDecision(
            action=action,
            next_role=next_role,
            should_continue=True,
            confidence=self._normalize_confidence(
                getattr(
                    routing,
                    "confidence",
                    0.50,
                )
            ),
            reason=str(
                getattr(
                    routing,
                    "reason",
                    "Routing selesai.",
                )
            ),
            metadata={
                "routing_role":
                    getattr(
                        routing,
                        "role",
                        None,
                    ),
            },
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

        if not isinstance(role, str):
            raise TypeError(
                "role harus string."
            )

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        method = getattr(
            self.role_engine,
            "run",
            None,
        )

        if not callable(method):
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
        Membuat bridge:

            ExecutionEngineV2
                    ↓
              RoleEngine
                    ↓
              GeminiAdapter
        """

        def handler(
            execution_context: Any,
        ) -> dict[str, Any]:

            previous_result = ""

            previous_results = getattr(
                execution_context,
                "previous_results",
                None,
            )

            if previous_results:

                try:
                    previous = (
                        previous_results[-1]
                    )

                    previous_result = (
                        self._extract_output(
                            previous
                        )
                    )

                except (
                    IndexError,
                    TypeError,
                ):
                    previous_result = ""

            role = self._to_text(
                getattr(
                    execution_context,
                    "role",
                    "",
                )
            ).strip()

            task = self._to_text(
                getattr(
                    execution_context,
                    "task",
                    "",
                )
            ).strip()

            context = self._to_text(
                getattr(
                    execution_context,
                    "context",
                    "",
                )
            )

            if not role:
                raise RuntimeError(
                    "Execution context tidak memiliki role."
                )

            if not task:
                raise RuntimeError(
                    "Execution context tidak memiliki task."
                )

            role_result = self.run_role(
                role=role,
                task=task,
                context=context,
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

            normalized_status = (
                status.strip().lower()
                if isinstance(
                    status,
                    str,
                )
                else str(status).strip().lower()
            )

            failure_statuses = {
                "failed",
                "failure",
                "error",
                "errored",
                "cancelled",
                "canceled",
                "aborted",
            }

            if (
                normalized_status
                in failure_statuses
                or success is False
            ):

                error = self._extract_error(
                    role_result
                )

                raise RuntimeError(
                    error
                    or (
                        f"Role '{role}' "
                        "gagal dieksekusi."
                    )
                )

            return {
                "output": output,
                "metadata": {
                    "role": role,
                    "runtime": RUNTIME_NAME,
                    "status":
                        normalized_status
                        or "success",
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

        Pipeline default:

            planner
              ↓
            coder
              ↓
            reviewer
              ↓
            tester

        Jika ExecutionEngineV2 memiliki repair/debug flow,
        engine tersebut menjadi source of truth untuk siklus
        repair.
        """

        if not pipeline:
            raise ValueError(
                "Pipeline tidak boleh kosong."
            )

        handler = self._create_execution_handler(
            memory_context=memory_context,
        )

        register = getattr(
            self.execution,
            "register",
            None,
        )

        if not callable(register):
            raise RuntimeError(
                "ExecutionEngineV2 tidak memiliki "
                "method register()."
            )

        # ----------------------------------------------------
        # REGISTER ROLE HANDLERS
        # ----------------------------------------------------

        for role in pipeline:

            if not isinstance(
                role,
                str,
            ):
                raise TypeError(
                    "Semua pipeline role harus string."
                )

            normalized_role = role.strip().lower()

            if not normalized_role:
                raise ValueError(
                    "Pipeline mengandung role kosong."
                )

            register(
                normalized_role,
                handler,
            )

        # ----------------------------------------------------
        # EXECUTE
        # ----------------------------------------------------

        execute = getattr(
            self.execution,
            "execute",
            None,
        )

        if not callable(execute):
            raise RuntimeError(
                "ExecutionEngineV2 tidak memiliki "
                "method execute()."
            )

        attempts = (
            {
                "task": task,
                "pipeline": list(pipeline),
                "context": context,
            },
            {
                "task": task,
                "pipeline": list(pipeline),
            },
        )

        last_error: Optional[Exception] = None

        for kwargs in attempts:

            try:
                return execute(
                    **kwargs
                )

            except TypeError as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "ExecutionEngineV2 gagal menjalankan pipeline."
        )

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

            task
              ↓
            router
              ↓
            context
              ↓
            memory
              ↓
            decision
              ↓
            execution engine
              ↓
            role engine
              ↓
            Gemini

        Pipeline:

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
                    "runtime":
                        RUNTIME_NAME,
                    "root":
                        str(self.root),
                    "pipeline":
                        list(DEFAULT_PIPELINE),
                },
            )

        normalized_task = task.strip()

        # ----------------------------------------------------
        # ROUTE
        # ----------------------------------------------------

        routing = self.route(
            normalized_task
        )

        routing_role = self._normalize_role(
            getattr(
                routing,
                "role",
                None,
            )
        )

        routing_confidence = (
            self._normalize_confidence(
                getattr(
                    routing,
                    "confidence",
                    0.50,
                )
            )
        )

        routing_reason = str(
            getattr(
                routing,
                "reason",
                "Routing selesai.",
            )
        )

        # ----------------------------------------------------
        # CONTEXT
        # ----------------------------------------------------

        if context is None:

            context_result = self.build_context(
                normalized_task,
                role=routing_role,
                max_files=max_files,
                max_chars=max_chars,
            )

            context_text = self._to_text(
                context_result
            )

        else:

            context_text = (
                context
                if isinstance(
                    context,
                    str,
                )
                else str(context)
            )

        context_text = context_text.strip()

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

            memory_text = (
                memory_context
                if isinstance(
                    memory_context,
                    str,
                )
                else str(memory_context)
            )

        memory_text = memory_text.strip()

        # ----------------------------------------------------
        # DECISION
        # ----------------------------------------------------

        decision = self.decide(
            task=normalized_task,
            role=routing_role,
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
                confidence=(
                    self._normalize_confidence(
                        decision.confidence
                    )
                ),
                reason=decision.reason,
                metadata={
                    "runtime":
                        RUNTIME_NAME,
                    "root":
                        str(self.root),
                    "routing_role":
                        routing_role,
                    "routing_confidence":
                        routing_confidence,
                    "routing_reason":
                        routing_reason,
                    "pipeline":
                        list(DEFAULT_PIPELINE),
                },
            )

        # ----------------------------------------------------
        # PUBLIC ROLE
        # ----------------------------------------------------
        #
        # Router menentukan role domain utama.
        #
        # Namun execution pipeline tetap menggunakan:
        #
        # planner -> coder -> reviewer -> tester
        #
        # Hal ini penting supaya task coding tidak langsung
        # melewati planning/review/testing.
        #

        role = routing_role

        if not role and decision.next_role:
            role = self._normalize_role(
                decision.next_role
            )

        if not role:
            role = "planner"

        # ----------------------------------------------------
        # PIPELINE
        # ----------------------------------------------------

        pipeline = list(
            DEFAULT_PIPELINE
        )

        # ----------------------------------------------------
        # EXECUTION
        # ----------------------------------------------------

        try:

            execution_state = (
                self._execute_pipeline(
                    task=normalized_task,
                    pipeline=pipeline,
                    context=context_text,
                    memory_context=memory_text,
                )
            )

        except Exception as error:

            error_text = self._sanitize_error(
                str(error)
            )

            return AgentRuntimeResult(
                task=normalized_task,
                action=ACTION_DEBUG,
                status="failed",
                role=role,
                confidence=(
                    min(
                        routing_confidence,
                        0.50,
                    )
                ),
                output="",
                reason=(
                    "Execution pipeline gagal: "
                    + error_text
                ),
                repair_rounds=0,
                metadata={
                    "runtime":
                        RUNTIME_NAME,
                    "root":
                        str(self.root),
                    "routing_role":
                        routing_role,
                    "routing_confidence":
                        routing_confidence,
                    "pipeline":
                        list(pipeline),
                    "decision_action":
                        decision.action,
                    "decision_next_role":
                        decision.next_role,
                    "execution_error":
                        error_text,
                },
            )

        # ----------------------------------------------------
        # PUBLIC OUTPUT
        # ----------------------------------------------------

        execution_results = (
            self._extract_execution_results(
                execution_state
            )
        )

        output = ""

        # Cari output dari role yang dirouting.
        for execution_result in execution_results:

            result_role = self._normalize_role(
                getattr(
                    execution_result,
                    "role",
                    None,
                )
            )

            if (
                result_role
                == role
            ):

                output = self._extract_output(
                    execution_result
                )

                if output:
                    break

        # Fallback ke hasil terakhir.
        if (
            not output
            and execution_results
        ):

            output = self._extract_output(
                execution_results[-1]
            )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        execution_status = (
            self._extract_execution_status(
                execution_state
            )
        )

        if not execution_status:
            execution_status = (
                "success"
                if execution_results
                else "failed"
            )

        normalized_execution_status = (
            execution_status.strip().lower()
        )

        if normalized_execution_status in {
            "success",
            "completed",
            "complete",
            "ok",
            "passed",
            "pass",
        }:

            status = "success"

        elif normalized_execution_status in {
            "failed",
            "failure",
            "error",
            "errored",
            "cancelled",
            "canceled",
            "aborted",
        }:

            status = "failed"

        else:
            status = normalized_execution_status

        # ----------------------------------------------------
        # EXECUTION METADATA
        # ----------------------------------------------------

        execution_metadata = (
            self._safe_to_dict(
                execution_state
            )
        )

        repair_round = (
            self._extract_repair_round(
                execution_state
            )
        )

        # ----------------------------------------------------
        # FINAL RESULT
        # ----------------------------------------------------

        return AgentRuntimeResult(
            task=normalized_task,
            action=(
                ACTION_FINISH
                if status == "success"
                else decision.action
            ),
            status=status,
            role=role,
            confidence=(
                self._normalize_confidence(
                    decision.confidence
                )
            ),
            output=output,
            reason=decision.reason,
            repair_rounds=repair_round,
            metadata={
                "runtime":
                    RUNTIME_NAME,

                "root":
                    str(self.root),

                "routing_role":
                    routing_role,

                "routing_confidence":
                    routing_confidence,

                "routing_reason":
                    routing_reason,

                "pipeline":
                    list(pipeline),

                "execution_role":
                    role,

                "execution_status":
                    execution_status,

                "execution_results_count":
                    len(execution_results),

                "execution":
                    execution_metadata,

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
        - dict
        - AgentExecutionResult
        - RoleExecutionResult
        - object.text
        - object.output
        - object.content
        - object.result
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

                if value is None:
                    continue

                if isinstance(
                    value,
                    str,
                ):
                    return value

                if isinstance(
                    value,
                    (dict, list),
                ):
                    return str(value)

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

            if value is None:
                continue

            if isinstance(
                value,
                str,
            ):
                return value

            if isinstance(
                value,
                (dict, list),
            ):
                return str(value)

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

            return str(status)

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
            None
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
    # EXECUTION RESULT EXTRACTION
    # ========================================================

    @staticmethod
    def _extract_execution_results(
        execution_state: Any,
    ) -> list[Any]:
        """
        Ambil execution results sebagai LIST.

        Ini penting karena ExecutionEngineV2 menyimpan
        banyak result, bukan satu RoleExecutionResult.
        """

        if execution_state is None:
            return []

        if isinstance(
            execution_state,
            dict,
        ):

            results = execution_state.get(
                "results"
            )

            if results is None:
                return []

            if isinstance(
                results,
                list,
            ):
                return list(results)

            if isinstance(
                results,
                tuple,
            ):
                return list(results)

            return [results]

        results = getattr(
            execution_state,
            "results",
            None,
        )

        if results is None:
            return []

        if isinstance(
            results,
            list,
        ):
            return list(results)

        if isinstance(
            results,
            tuple,
        ):
            return list(results)

        try:
            return list(results)

        except TypeError:
            return [results]

    # ========================================================
    # EXECUTION STATUS
    # ========================================================

    @staticmethod
    def _extract_execution_status(
        execution_state: Any,
    ) -> str:
        """
        Ambil status dari ExecutionState.
        """

        if execution_state is None:
            return ""

        if isinstance(
            execution_state,
            dict,
        ):

            status = execution_state.get(
                "status"
            )

            if status is not None:
                return str(status)

            if execution_state.get(
                "success"
            ) is True:
                return "success"

            if execution_state.get(
                "success"
            ) is False:
                return "failed"

            return ""

        status = getattr(
            execution_state,
            "status",
            None,
        )

        if status is not None:
            return str(status)

        success = getattr(
            execution_state,
            "success",
            None,
        )

        if success is True:
            return "success"

        if success is False:
            return "failed"

        return ""

    # ========================================================
    # REPAIR ROUND
    # ========================================================

    @staticmethod
    def _extract_repair_round(
        execution_state: Any,
    ) -> int:
        """
        Ambil repair round dari execution state.
        """

        if execution_state is None:
            return 0

        if isinstance(
            execution_state,
            dict,
        ):

            value = execution_state.get(
                "repair_round",
                0,
            )

        else:

            value = getattr(
                execution_state,
                "repair_round",
                0,
            )

        try:

            normalized = int(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0

        return max(
            0,
            normalized,
        )

    # ========================================================
    # SAFE DICT
    # ========================================================

    @staticmethod
    def _safe_to_dict(
        value: Any,
    ) -> dict[str, Any]:
        """
        Convert object menjadi dictionary jika memungkinkan.

        Tidak pernah melempar exception hanya karena
        observability gagal.
        """

        if value is None:
            return {}

        if isinstance(
            value,
            dict,
        ):
            return dict(value)

        method = getattr(
            value,
            "to_dict",
            None,
        )

        if callable(method):

            try:

                result = method()

                if isinstance(
                    result,
                    dict,
                ):
                    return dict(result)

            except Exception:
                pass

        try:

            return {
                "value": str(value)
            }

        except Exception:
            return {}

    # ========================================================
    # ROLE NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize_role(
        role: Any,
    ) -> str:
        """
        Normalisasi role secara aman.
        """

        if role is None:
            return ""

        value = getattr(
            role,
            "value",
            role,
        )

        return str(
            value
        ).strip().lower()

    # ========================================================
    # CONFIDENCE NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize_confidence(
        value: Any,
    ) -> float:
        """
        Normalisasi confidence ke 0.0 - 1.0.
        """

        try:

            confidence = float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0.0

        if confidence < 0.0:
            return 0.0

        if confidence > 1.0:
            return 1.0

        return confidence

    # ========================================================
    # ERROR SANITIZATION
    # ========================================================

    @staticmethod
    def _sanitize_error(
        error: Any,
    ) -> str:
        """
        Sanitasi error agar credential tidak bocor ke caller.

        Error provider yang mengandung marker credential
        diganti dengan pesan generik.
        """

        if error is None:
            return ""

        text = str(error)

        lowered = text.lower()

        sensitive_markers = (
            "api_key",
            "apikey",
            "authorization",
            "bearer",
            "secret",
            "password",
            "credential",
            "access_token",
            "refresh_token",
            "private_key",
        )

        if any(
            marker in lowered
            for marker in sensitive_markers
        ):
            return (
                "Provider mengalami "
                "authentication/provider error."
            )

        return text

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

        # Defensive sanitization terhadap provider status.
        adapter_status = self._sanitize_status_dict(
            adapter_status
        )

        return {
            "runtime":
                RUNTIME_NAME,

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

            "pipeline":
                list(DEFAULT_PIPELINE),
        }

    # ========================================================
    # STATUS SANITIZATION
    # ========================================================

    @classmethod
    def _sanitize_status_dict(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Sanitasi recursive dictionary sederhana.

        Key credential tidak dikembalikan.
        """

        blocked_keys = {
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

        sanitized: dict[str, Any] = {}

        for key, item in value.items():

            normalized_key = str(
                key
            ).strip().lower()

            if normalized_key in blocked_keys:
                continue

            if isinstance(
                item,
                dict,
            ):

                sanitized[key] = (
                    cls._sanitize_status_dict(
                        item
                    )
                )

            elif isinstance(
                item,
                list,
            ):

                sanitized[key] = [
                    (
                        cls._sanitize_status_dict(
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
                sanitized[key] = item

        return sanitized


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
    "DEFAULT_PIPELINE",
    "create_agent_runtime",
]
