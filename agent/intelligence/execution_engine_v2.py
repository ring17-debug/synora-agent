"""
Synora Execution Engine V2.

State-aware execution layer untuk multi-agent pipeline.

Fungsi utama:
- menjalankan role secara berurutan
- membawa state antar-agent
- menyimpan hasil setiap agent
- mencatat execution history
- mendukung retry/repair round
- tidak membocorkan API key
- tetap kompatibel dengan satu Gemini API key
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional


# ============================================================
# CONSTANTS
# ============================================================

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ABORTED = "aborted"

MAX_REPAIR_ROUNDS = 2


# ============================================================
# AGENT EXECUTION RESULT
# ============================================================

@dataclass
class AgentExecutionResult:
    """
    Hasil eksekusi satu agent.
    """

    role: str
    status: str
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# EXECUTION STATE
# ============================================================

@dataclass
class ExecutionState:
    """
    State global yang dibawa sepanjang pipeline.

    State ini sengaja tidak menyimpan API key.
    """

    task: str

    current_role: Optional[str] = None

    status: str = STATUS_PENDING

    repair_round: int = 0

    context: str = ""

    plan: str = ""

    changes: list[dict[str, Any]] = field(
        default_factory=list
    )

    verification: dict[str, Any] = field(
        default_factory=dict
    )

    results: list[AgentExecutionResult] = field(
        default_factory=list
    )

    history: list[dict[str, Any]] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def add_history(
        self,
        event: str,
        **data: Any,
    ) -> None:
        """
        Menambahkan event execution.

        Jangan pernah memasukkan secret ke data.
        """

        self.history.append(
            {
                "event": event,
                **data,
            }
        )

    def add_result(
        self,
        result: AgentExecutionResult,
    ) -> None:
        self.results.append(result)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)

        data["results"] = [
            result.to_dict()
            for result in self.results
        ]

        return data


# ============================================================
# EXECUTION CONTEXT
# ============================================================

@dataclass(frozen=True)
class AgentExecutionContext:
    """
    Context read-only yang diberikan kepada agent.

    Agent tidak menerima object internal engine secara langsung.
    """

    task: str
    role: str
    context: str
    plan: str
    changes: tuple[dict[str, Any], ...]
    verification: dict[str, Any]
    repair_round: int
    previous_results: tuple[
        AgentExecutionResult,
        ...
    ]


# ============================================================
# HANDLER
# ============================================================

AgentHandler = Callable[
    [AgentExecutionContext],
    Any,
]


# ============================================================
# ENGINE
# ============================================================

class ExecutionEngineV2:
    """
    State-aware execution engine.

    Contoh:

        engine = ExecutionEngineV2()

        engine.register(
            "planner",
            planner_handler,
        )

        engine.register(
            "coder",
            coder_handler,
        )

        state = engine.execute(
            "buat endpoint RPC baru",
            ["planner", "coder"],
        )
    """

    def __init__(
        self,
        *,
        max_repair_rounds: int = MAX_REPAIR_ROUNDS,
    ) -> None:

        if max_repair_rounds < 0:
            raise ValueError(
                "max_repair_rounds tidak boleh negatif."
            )

        self.max_repair_rounds = (
            max_repair_rounds
        )

        self.handlers: dict[
            str,
            AgentHandler,
        ] = {}

    # --------------------------------------------------------
    # REGISTER
    # --------------------------------------------------------

    def register(
        self,
        role: str,
        handler: AgentHandler,
    ) -> None:
        """
        Mendaftarkan handler untuk role.
        """

        if not isinstance(role, str):
            raise TypeError(
                "role harus string."
            )

        role = role.strip()

        if not role:
            raise ValueError(
                "role tidak boleh kosong."
            )

        if not callable(handler):
            raise TypeError(
                "handler harus callable."
            )

        self.handlers[role] = handler

    # --------------------------------------------------------
    # CONTEXT
    # --------------------------------------------------------

    @staticmethod
    def _build_context(
        state: ExecutionState,
        role: str,
    ) -> AgentExecutionContext:

        return AgentExecutionContext(
            task=state.task,
            role=role,
            context=state.context,
            plan=state.plan,
            changes=tuple(
                dict(item)
                for item in state.changes
            ),
            verification=dict(
                state.verification
            ),
            repair_round=state.repair_round,
            previous_results=tuple(
                state.results
            ),
        )

    # --------------------------------------------------------
    # OUTPUT NORMALIZATION
    # --------------------------------------------------------

    @staticmethod
    def _normalize_output(
        output: Any,
    ) -> str:
        """
        Normalisasi output agent menjadi string.

        Dict/list tetap aman untuk engine,
        tetapi representasi internal dibuat deterministic.
        """

        if output is None:
            return ""

        if isinstance(output, str):
            return output

        return str(output)

    # --------------------------------------------------------
    # RESULT APPLICATION
    # --------------------------------------------------------

    @staticmethod
    def _apply_output(
        state: ExecutionState,
        role: str,
        output: Any,
    ) -> str:
        """
        Terapkan output agent ke state.

        Handler boleh mengembalikan:

        - string
        - dict dengan field:
          output
          plan
          changes
          verification
          metadata
        """

        if isinstance(output, dict):

            text = output.get(
                "output",
                "",
            )

            if text is None:
                text = ""

            if "plan" in output:
                plan = output["plan"]

                if isinstance(
                    plan,
                    str,
                ):
                    state.plan = plan

            if "changes" in output:
                changes = output["changes"]

                if isinstance(
                    changes,
                    list,
                ):
                    state.changes = [
                        dict(item)
                        for item in changes
                        if isinstance(
                            item,
                            dict,
                        )
                    ]

            if "verification" in output:
                verification = (
                    output["verification"]
                )

                if isinstance(
                    verification,
                    dict,
                ):
                    state.verification = dict(
                        verification
                    )

            if "metadata" in output:
                metadata = output["metadata"]

                if isinstance(
                    metadata,
                    dict,
                ):
                    state.metadata.update(
                        metadata
                    )

            return ExecutionEngineV2._normalize_output(
                text
            )

        return ExecutionEngineV2._normalize_output(
            output
        )

    # --------------------------------------------------------
    # SINGLE AGENT
    # --------------------------------------------------------

    def execute_role(
        self,
        state: ExecutionState,
        role: str,
    ) -> AgentExecutionResult:
        """
        Jalankan satu role.
        """

        if role not in self.handlers:
            result = AgentExecutionResult(
                role=role,
                status=STATUS_FAILED,
                error=(
                    f"Handler untuk role "
                    f"'{role}' tidak terdaftar."
                ),
            )

            state.add_result(result)

            state.add_history(
                "agent_failed",
                role=role,
                reason="handler_missing",
            )

            return result

        state.current_role = role
        state.status = STATUS_RUNNING

        state.add_history(
            "agent_started",
            role=role,
            repair_round=state.repair_round,
        )

        context = self._build_context(
            state,
            role,
        )

        handler = self.handlers[role]

        try:
            output = handler(context)

            text = self._apply_output(
                state,
                role,
                output,
            )

            result = AgentExecutionResult(
                role=role,
                status=STATUS_SUCCESS,
                output=text,
            )

            state.add_result(result)

            state.add_history(
                "agent_completed",
                role=role,
                status=STATUS_SUCCESS,
            )

            return result

        except Exception as error:
            result = AgentExecutionResult(
                role=role,
                status=STATUS_FAILED,
                error=str(error),
            )

            state.add_result(result)

            state.status = STATUS_FAILED

            state.add_history(
                "agent_failed",
                role=role,
                status=STATUS_FAILED,
                error=str(error),
            )

            return result

    # --------------------------------------------------------
    # PIPELINE
    # --------------------------------------------------------

    def execute(
        self,
        task: str,
        pipeline: list[str],
        *,
        context: str = "",
        repair_round: int = 0,
    ) -> ExecutionState:
        """
        Menjalankan pipeline dari awal.

        Pipeline contoh:

            [
                "planner",
                "coder",
                "reviewer",
                "tester",
            ]
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

        if not isinstance(
            pipeline,
            list,
        ):
            raise TypeError(
                "pipeline harus list."
            )

        if not pipeline:
            raise ValueError(
                "pipeline tidak boleh kosong."
            )

        state = ExecutionState(
            task=task.strip(),
            context=context,
            repair_round=repair_round,
        )

        state.add_history(
            "execution_started",
            pipeline=list(pipeline),
        )

        for role in pipeline:

            result = self.execute_role(
                state,
                role,
            )

            if result.status != STATUS_SUCCESS:
                state.status = STATUS_FAILED

                state.add_history(
                    "execution_stopped",
                    role=role,
                    reason="agent_failed",
                )

                return state

        state.status = STATUS_SUCCESS

        state.add_history(
            "execution_completed",
            status=STATUS_SUCCESS,
        )

        return state

    # --------------------------------------------------------
    # REPAIR
    # --------------------------------------------------------

    def execute_repair(
        self,
        state: ExecutionState,
        *,
        debugger_role: str = "debugger",
        coder_role: str = "coder",
        reviewer_role: str = "reviewer",
        tester_role: str = "tester",
    ) -> ExecutionState:
        """
        Jalankan repair cycle:

            debugger
                ↓
            coder
                ↓
            reviewer
                ↓
            tester

        Repair dibatasi max_repair_rounds.
        """

        if (
            state.repair_round
            >= self.max_repair_rounds
        ):
            state.status = STATUS_ABORTED

            state.add_history(
                "repair_aborted",
                reason="repair_limit",
                repair_round=state.repair_round,
            )

            return state

        state.repair_round += 1

        state.add_history(
            "repair_started",
            repair_round=state.repair_round,
        )

        roles = [
            debugger_role,
            coder_role,
            reviewer_role,
            tester_role,
        ]

        for role in roles:

            result = self.execute_role(
                state,
                role,
            )

            if result.status != STATUS_SUCCESS:
                state.status = STATUS_FAILED

                state.add_history(
                    "repair_stopped",
                    role=role,
                    repair_round=state.repair_round,
                )

                return state

        state.status = STATUS_SUCCESS

        state.add_history(
            "repair_completed",
            repair_round=state.repair_round,
        )

        return state

    # --------------------------------------------------------
    # SERIALIZATION
    # --------------------------------------------------------

    @staticmethod
    def serialize(
        state: ExecutionState,
    ) -> dict[str, Any]:
        """
        Serialize state menjadi dictionary.
        """

        if not isinstance(
            state,
            ExecutionState,
        ):
            raise TypeError(
                "state harus ExecutionState."
            )

        return state.to_dict()

    # --------------------------------------------------------
    # SAFE STATUS
    # --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """
        Status engine yang aman.

        Tidak mengembalikan handler internal,
        API key, atau credential.
        """

        return {
            "engine": "execution_v2",
            "registered_roles": sorted(
                self.handlers.keys()
            ),
            "max_repair_rounds": (
                self.max_repair_rounds
            ),
        }


# ============================================================
# DEFAULT ENGINE
# ============================================================

def create_execution_engine(
    *,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
) -> ExecutionEngineV2:
    """
    Factory untuk Execution Engine V2.
    """

    return ExecutionEngineV2(
        max_repair_rounds=max_repair_rounds,
    )


__all__ = [
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_SUCCESS",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STATUS_ABORTED",
    "MAX_REPAIR_ROUNDS",
    "AgentExecutionResult",
    "ExecutionState",
    "AgentExecutionContext",
    "ExecutionEngineV2",
    "create_execution_engine",
]
