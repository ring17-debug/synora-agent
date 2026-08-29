"""
Synora Execution Engine V2.2.

State-aware execution layer untuk multi-agent pipeline.

Fitur:
- sequential multi-agent execution
- state antar-role
- structured result
- plan / changes / verification propagation
- execution history
- repair cycle
- handler registration
- backward-compatible result normalization
- safe serialization
- tidak menyimpan credential/API key
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
    Hasil eksekusi satu role.
    """

    role: str
    status: str
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# EXECUTION STATE
# ============================================================

@dataclass
class ExecutionState:
    """
    State global pipeline.

    State ini tidak boleh menyimpan credential.
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
        Tambahkan event execution.

        Caller bertanggung jawab untuk tidak memasukkan secret.
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
    Context read-only yang diberikan kepada role handler.
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
        ...,
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
    State-aware multi-agent execution engine.
    """

    def __init__(
        self,
        *,
        max_repair_rounds: int = MAX_REPAIR_ROUNDS,
    ) -> None:

        if not isinstance(
            max_repair_rounds,
            int,
        ):
            raise TypeError(
                "max_repair_rounds harus integer."
            )

        if max_repair_rounds < 0:
            raise ValueError(
                "max_repair_rounds tidak boleh negatif."
            )

        self.max_repair_rounds = max_repair_rounds

        self.handlers: dict[
            str,
            AgentHandler,
        ] = {}

    # ========================================================
    # REGISTER
    # ========================================================

    def register(
        self,
        role: str,
        handler: AgentHandler,
    ) -> None:
        """
        Register handler untuk sebuah role.
        """

        if not isinstance(
            role,
            str,
        ):
            raise TypeError(
                "role harus string."
            )

        normalized = role.strip()

        if not normalized:
            raise ValueError(
                "role tidak boleh kosong."
            )

        if not callable(handler):
            raise TypeError(
                "handler harus callable."
            )

        self.handlers[normalized] = handler

    # ========================================================
    # UNREGISTER
    # ========================================================

    def unregister(
        self,
        role: str,
    ) -> bool:
        """
        Menghapus handler role.
        """

        if not isinstance(
            role,
            str,
        ):
            raise TypeError(
                "role harus string."
            )

        normalized = role.strip()

        if not normalized:
            raise ValueError(
                "role tidak boleh kosong."
            )

        return self.handlers.pop(
            normalized,
            None,
        ) is not None

    # ========================================================
    # HAS HANDLER
    # ========================================================

    def has_handler(
        self,
        role: str,
    ) -> bool:
        """
        Mengecek handler.
        """

        if not isinstance(
            role,
            str,
        ):
            return False

        return role.strip() in self.handlers

    # ========================================================
    # CONTEXT
    # ========================================================

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
                if isinstance(item, dict)
            ),
            verification=dict(
                state.verification
            ),
            repair_round=state.repair_round,
            previous_results=tuple(
                state.results
            ),
        )

    # ========================================================
    # OUTPUT NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize_output(
        output: Any,
    ) -> str:
        """
        Normalisasi output menjadi string.
        """

        if output is None:
            return ""

        if isinstance(
            output,
            str,
        ):
            return output

        return str(output)

    # ========================================================
    # STRUCTURED OUTPUT
    # ========================================================

    @staticmethod
    def _coerce_structured_output(
        output: Any,
    ) -> dict[str, Any] | None:
        """
        Normalisasi berbagai bentuk result menjadi dictionary.

        Supported:

        - dict
        - object.to_dict()
        - object dengan structured attributes
        - string / primitive
        """

        if output is None:
            return None

        if isinstance(
            output,
            dict,
        ):
            return dict(output)

        to_dict = getattr(
            output,
            "to_dict",
            None,
        )

        if callable(to_dict):
            try:
                converted = to_dict()

                if isinstance(
                    converted,
                    dict,
                ):
                    return dict(converted)

            except Exception:
                pass

        result: dict[str, Any] = {}

        for key in (
            "output",
            "plan",
            "changes",
            "verification",
            "metadata",
            "error",
            "success",
            "status",
            "structured",
        ):
            try:
                value = getattr(
                    output,
                    key,
                    None,
                )
            except Exception:
                continue

            if value is not None:
                result[key] = value

        return result or None

    # ========================================================
    # UNWRAP STRUCTURED RESULT
    # ========================================================

    @staticmethod
    def _unwrap_structured_output(
        structured: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Mengambil structured payload dari result envelope.

        RoleExecutionResult biasanya menghasilkan:

            {
                "role": "...",
                "status": "success",
                "output": "...",
                "metadata": {...},
                "structured": {
                    "plan": "...",
                    "changes": [...],
                    "verification": {...},
                }
            }

        Engine harus membaca payload structured tersebut,
        tetapi tetap mempertahankan envelope untuk metadata/error.
        """

        if not structured:
            return structured

        nested = structured.get(
            "structured"
        )

        if not isinstance(
            nested,
            dict,
        ):
            return structured

        merged = dict(nested)

        # Preserve envelope-level fields when they are not
        # already present in the structured payload.
        for key in (
            "output",
            "error",
            "success",
            "status",
            "metadata",
            "role",
        ):
            if key not in merged and key in structured:
                merged[key] = structured[key]

        return merged

    # ========================================================
    # METADATA
    # ========================================================

    @staticmethod
    def _extract_metadata(
        structured: dict[str, Any] | None,
    ) -> dict[str, Any]:

        if not structured:
            return {}

        metadata = structured.get(
            "metadata"
        )

        if isinstance(
            metadata,
            dict,
        ):
            return dict(metadata)

        return {}

    # ========================================================
    # SUCCESS
    # ========================================================

    @staticmethod
    def _extract_success(
        structured: dict[str, Any] | None,
    ) -> Optional[bool]:

        if not structured:
            return None

        value = structured.get(
            "success"
        )

        if isinstance(
            value,
            bool,
        ):
            return value

        return None

    # ========================================================
    # ERROR
    # ========================================================

    @staticmethod
    def _extract_error(
        structured: dict[str, Any] | None,
    ) -> str:

        if not structured:
            return ""

        value = structured.get(
            "error"
        )

        if value is None:
            return ""

        return str(value)

    # ========================================================
    # STATUS
    # ========================================================

    @staticmethod
    def _extract_status(
        structured: dict[str, Any] | None,
    ) -> str:

        if not structured:
            return ""

        value = structured.get(
            "status"
        )

        if value is None:
            return ""

        return str(value).strip().lower()

    # ========================================================
    # APPLY OUTPUT
    # ========================================================

    @classmethod
    def _apply_output(
        cls,
        state: ExecutionState,
        role: str,
        output: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """
        Apply hasil role ke ExecutionState.

        Return:

            (
                output_text,
                metadata,
                structured_payload,
            )

        Mendukung dua bentuk:

        Legacy:

            {
                "plan": "...",
                "changes": [...],
                "verification": {...}
            }

        Structured envelope:

            {
                "output": "...",
                "structured": {
                    "plan": "...",
                    "changes": [...],
                    "verification": {...}
                }
            }
        """

        envelope = cls._coerce_structured_output(
            output
        )

        # ----------------------------------------------------
        # PLAIN OUTPUT
        # ----------------------------------------------------

        if envelope is None:

            text = cls._normalize_output(
                output
            )

            state.metadata[
                f"role_{role}_executed"
            ] = True

            return (
                text,
                {},
                {},
            )

        # ----------------------------------------------------
        # UNWRAP NESTED STRUCTURED PAYLOAD
        # ----------------------------------------------------

        structured = cls._unwrap_structured_output(
            envelope
        )

        if structured is None:
            structured = {}

        # ----------------------------------------------------
        # OUTPUT
        # ----------------------------------------------------

        text = envelope.get(
            "output",
            structured.get(
                "output",
                "",
            ),
        )

        if text is None:
            text = ""

        # ----------------------------------------------------
        # PLAN
        # ----------------------------------------------------

        if "plan" in structured:

            plan = structured.get(
                "plan"
            )

            if isinstance(
                plan,
                str,
            ):
                state.plan = plan

            elif plan is not None:
                state.plan = str(plan)

        # ----------------------------------------------------
        # CHANGES
        # ----------------------------------------------------

        if "changes" in structured:

            changes = structured.get(
                "changes"
            )

            if isinstance(
                changes,
                list,
            ):

                normalized_changes: list[
                    dict[str, Any]
                ] = []

                for item in changes:

                    if isinstance(
                        item,
                        dict,
                    ):
                        normalized_changes.append(
                            dict(item)
                        )

                state.changes = (
                    normalized_changes
                )

        # ----------------------------------------------------
        # VERIFICATION
        # ----------------------------------------------------

        if "verification" in structured:

            verification = structured.get(
                "verification"
            )

            if isinstance(
                verification,
                dict,
            ):
                state.verification = dict(
                    verification
                )

        # ----------------------------------------------------
        # METADATA
        # ----------------------------------------------------

        metadata = cls._extract_metadata(
            envelope
        )

        if not metadata:
            metadata = cls._extract_metadata(
                structured
            )

        if metadata:
            state.metadata.update(
                metadata
            )

        state.metadata[
            f"role_{role}_executed"
        ] = True

        return (
            cls._normalize_output(
                text
            ),
            metadata,
            structured,
        )

    # ========================================================
    # EXECUTE ROLE
    # ========================================================

    def execute_role(
        self,
        state: ExecutionState,
        role: str,
    ) -> AgentExecutionResult:
        """
        Jalankan satu role.
        """

        # ----------------------------------------------------
        # INVALID ROLE TYPE
        # ----------------------------------------------------

        if not isinstance(
            role,
            str,
        ):

            result = AgentExecutionResult(
                role="unknown",
                status=STATUS_FAILED,
                error="role harus string.",
            )

            state.add_result(result)

            state.status = STATUS_FAILED

            state.add_history(
                "agent_failed",
                role="unknown",
                reason="invalid_role",
            )

            return result

        # ----------------------------------------------------
        # NORMALIZE ROLE
        # ----------------------------------------------------

        role = role.strip()

        if not role:

            result = AgentExecutionResult(
                role="unknown",
                status=STATUS_FAILED,
                error="role tidak boleh kosong.",
            )

            state.add_result(result)

            state.status = STATUS_FAILED

            state.add_history(
                "agent_failed",
                role="unknown",
                reason="empty_role",
            )

            return result

        # ----------------------------------------------------
        # HANDLER
        # ----------------------------------------------------

        handler = self.handlers.get(
            role
        )

        if handler is None:

            result = AgentExecutionResult(
                role=role,
                status=STATUS_FAILED,
                error=(
                    f"Handler untuk role "
                    f"'{role}' tidak terdaftar."
                ),
            )

            state.add_result(result)

            state.status = STATUS_FAILED

            state.add_history(
                "agent_failed",
                role=role,
                reason="handler_missing",
            )

            return result

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # EXECUTION
        # ----------------------------------------------------

        try:

            output = handler(
                context
            )

            envelope = (
                self._coerce_structured_output(
                    output
                )
            )

            structured = (
                self._unwrap_structured_output(
                    envelope
                )
            )

            declared_success = (
                self._extract_success(
                    structured
                )
            )

            declared_error = (
                self._extract_error(
                    structured
                )
            )

            declared_status = (
                self._extract_status(
                    structured
                )
            )

            text, metadata, structured_payload = (
                self._apply_output(
                    state,
                    role,
                    output,
                )
            )

            # ------------------------------------------------
            # EXPLICIT FAILURE
            # ------------------------------------------------

            if declared_success is False:

                result = AgentExecutionResult(
                    role=role,
                    status=STATUS_FAILED,
                    output=text,
                    error=(
                        declared_error
                        or "Agent melaporkan kegagalan."
                    ),
                    metadata=metadata,
                    structured=structured_payload,
                )

                state.add_result(result)

                state.status = STATUS_FAILED

                state.add_history(
                    "agent_failed",
                    role=role,
                    status=STATUS_FAILED,
                    reason="agent_reported_failure",
                )

                return result

            # ------------------------------------------------
            # EXPLICIT FAILED STATUS
            # ------------------------------------------------

            if declared_status in {
                STATUS_FAILED,
                STATUS_ABORTED,
            }:

                result = AgentExecutionResult(
                    role=role,
                    status=STATUS_FAILED,
                    output=text,
                    error=(
                        declared_error
                        or (
                            f"Role '{role}' "
                            "mengembalikan status gagal."
                        )
                    ),
                    metadata=metadata,
                    structured=structured_payload,
                )

                state.add_result(result)

                state.status = STATUS_FAILED

                state.add_history(
                    "agent_failed",
                    role=role,
                    status=STATUS_FAILED,
                    reason="agent_reported_status",
                )

                return result

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            result = AgentExecutionResult(
                role=role,
                status=STATUS_SUCCESS,
                output=text,
                metadata=metadata,
                structured=structured_payload,
            )

            state.add_result(result)

            state.add_history(
                "agent_completed",
                role=role,
                status=STATUS_SUCCESS,
            )

            return result

        # ----------------------------------------------------
        # EXCEPTION
        # ----------------------------------------------------

        except Exception as error:

            error_text = str(error)

            result = AgentExecutionResult(
                role=role,
                status=STATUS_FAILED,
                error=error_text,
            )

            state.add_result(result)

            state.status = STATUS_FAILED

            state.add_history(
                "agent_failed",
                role=role,
                status=STATUS_FAILED,
                error=error_text,
            )

            return result

    # ========================================================
    # PIPELINE
    # ========================================================

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
        """

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not isinstance(
            task,
            str,
        ):
            raise TypeError(
                "task harus string."
            )

        normalized_task = task.strip()

        if not normalized_task:
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

        if repair_round < 0:
            raise ValueError(
                "repair_round tidak boleh negatif."
            )

        # ----------------------------------------------------
        # NORMALIZE PIPELINE
        # ----------------------------------------------------

        normalized_pipeline: list[str] = []

        for role in pipeline:

            if not isinstance(
                role,
                str,
            ):
                raise TypeError(
                    "setiap role dalam pipeline "
                    "harus string."
                )

            normalized_role = role.strip()

            if not normalized_role:
                raise ValueError(
                    "role pipeline tidak boleh kosong."
                )

            normalized_pipeline.append(
                normalized_role
            )

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        state = ExecutionState(
            task=normalized_task,
            context=(
                context
                if isinstance(
                    context,
                    str,
                )
                else str(context)
            ),
            repair_round=repair_round,
        )

        state.add_history(
            "execution_started",
            pipeline=list(
                normalized_pipeline
            ),
            repair_round=repair_round,
        )

        # ----------------------------------------------------
        # EXECUTE
        # ----------------------------------------------------

        for role in normalized_pipeline:

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

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        state.status = STATUS_SUCCESS

        state.current_role = (
            normalized_pipeline[-1]
        )

        state.add_history(
            "execution_completed",
            status=STATUS_SUCCESS,
        )

        return state

    # ========================================================
    # REPAIR
    # ========================================================

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
        """

        if not isinstance(
            state,
            ExecutionState,
        ):
            raise TypeError(
                "state harus ExecutionState."
            )

        # ----------------------------------------------------
        # LIMIT
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # VALIDATE ROLES
        # ----------------------------------------------------

        repair_roles = [
            debugger_role,
            coder_role,
            reviewer_role,
            tester_role,
        ]

        for role in repair_roles:

            if not isinstance(
                role,
                str,
            ) or not role.strip():

                state.status = STATUS_FAILED

                state.add_history(
                    "repair_failed",
                    reason="invalid_repair_role",
                )

                return state

        # ----------------------------------------------------
        # INCREMENT ROUND
        # ----------------------------------------------------

        state.repair_round += 1

        state.status = STATUS_RUNNING

        state.add_history(
            "repair_started",
            repair_round=state.repair_round,
        )

        # ----------------------------------------------------
        # EXECUTE
        # ----------------------------------------------------

        for role in repair_roles:

            result = self.execute_role(
                state,
                role.strip(),
            )

            if result.status != STATUS_SUCCESS:

                state.status = STATUS_FAILED

                state.add_history(
                    "repair_stopped",
                    role=role,
                    repair_round=state.repair_round,
                )

                return state

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        state.status = STATUS_SUCCESS

        state.add_history(
            "repair_completed",
            repair_round=state.repair_round,
        )

        return state

    # ========================================================
    # SERIALIZATION
    # ========================================================

    @staticmethod
    def serialize(
        state: ExecutionState,
    ) -> dict[str, Any]:
        """
        Serialize ExecutionState.
        """

        if not isinstance(
            state,
            ExecutionState,
        ):
            raise TypeError(
                "state harus ExecutionState."
            )

        return state.to_dict()

    # ========================================================
    # SAFE STATUS
    # ========================================================

    def status(self) -> dict[str, Any]:
        """
        Status aman engine.

        Tidak mengembalikan handler object,
        credential, atau API key.
        """

        return {
            "engine": "execution_v2",
            "registered_roles": sorted(
                self.handlers.keys()
            ),
            "max_repair_rounds":
                self.max_repair_rounds,
        }


# ============================================================
# FACTORY
# ============================================================

def create_execution_engine(
    *,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
) -> ExecutionEngineV2:
    """
    Factory ExecutionEngineV2.
    """

    return ExecutionEngineV2(
        max_repair_rounds=max_repair_rounds,
    )


# ============================================================
# EXPORTS
# ============================================================

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
    "AgentHandler",
    "ExecutionEngineV2",
    "create_execution_engine",
]
