"""
Synora Execution Engine V1.

Tanggung jawab:
- menerima keputusan dari Decision Engine
- menentukan apakah execution boleh dilakukan
- menjaga state execution
- membatasi role yang boleh melakukan action
- mencatat setiap transition
- tidak menjalankan shell command berbahaya
- tidak menyentuh API key / secret
- menjadi fondasi untuk verification dan repair loop

Execution Engine TIDAK melakukan:
- routing
- membuat patch
- menjalankan cargo
- mengubah Git
- membaca secret

Execution Engine hanya mengatur STATE eksekusi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# ============================================================
# ACTIONS
# ============================================================

ACTION_PLAN = "plan"
ACTION_CODE = "code"
ACTION_REVIEW = "review"
ACTION_TEST = "test"
ACTION_DEBUG = "debug"
ACTION_REPAIR = "repair"
ACTION_FINISH = "finish"
ACTION_ABORT = "abort"


VALID_ACTIONS = {
    ACTION_PLAN,
    ACTION_CODE,
    ACTION_REVIEW,
    ACTION_TEST,
    ACTION_DEBUG,
    ACTION_REPAIR,
    ACTION_FINISH,
    ACTION_ABORT,
}


# ============================================================
# EXECUTION STATES
# ============================================================

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_WAITING = "waiting"
STATE_FINISHED = "finished"
STATE_ABORTED = "aborted"


# ============================================================
# RESULT
# ============================================================


@dataclass(frozen=True)
class ExecutionResult:
    """
    Hasil satu transition execution.
    """

    accepted: bool
    state: str
    action: str
    role: Optional[str]
    reason: str
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "state": self.state,
            "action": self.action,
            "role": self.role,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


# ============================================================
# EXECUTION EVENT
# ============================================================


@dataclass(frozen=True)
class ExecutionEvent:
    """
    Audit event internal execution engine.

    Tidak menyimpan secret.
    """

    timestamp: str
    action: str
    role: Optional[str]
    previous_state: str
    next_state: str
    accepted: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "role": self.role,
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "accepted": self.accepted,
            "reason": self.reason,
        }


# ============================================================
# ENGINE
# ============================================================


class ExecutionEngine:
    """
    State machine execution Synora.

    Execution Engine tidak menjalankan command.

    Ia hanya menentukan apakah sebuah action
    valid untuk state saat ini.
    """

    ROLE_ACTIONS = {
        "planner": {
            ACTION_PLAN,
        },
        "coder": {
            ACTION_CODE,
            ACTION_REPAIR,
        },
        "reviewer": {
            ACTION_REVIEW,
        },
        "tester": {
            ACTION_TEST,
        },
        "debugger": {
            ACTION_DEBUG,
            ACTION_REPAIR,
        },
    }

    ACTION_ROLE = {
        ACTION_PLAN: "planner",
        ACTION_CODE: "coder",
        ACTION_REVIEW: "reviewer",
        ACTION_TEST: "tester",
        ACTION_DEBUG: "debugger",
        ACTION_REPAIR: "coder",
    }

    def __init__(self) -> None:
        self.state = STATE_IDLE
        self.history: list[ExecutionEvent] = []

    # ========================================================
    # INTERNAL
    # ========================================================

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().isoformat(
            timespec="seconds"
        )

    def _event(
        self,
        *,
        action: str,
        role: Optional[str],
        previous_state: str,
        next_state: str,
        accepted: bool,
        reason: str,
    ) -> None:
        self.history.append(
            ExecutionEvent(
                timestamp=self._timestamp(),
                action=action,
                role=role,
                previous_state=previous_state,
                next_state=next_state,
                accepted=accepted,
                reason=reason,
            )
        )

    # ========================================================
    # VALIDATION
    # ========================================================

    @staticmethod
    def _validate_action(
        action: str,
    ) -> Optional[str]:

        if not isinstance(action, str):
            return "action harus string."

        if action not in VALID_ACTIONS:
            return f"action tidak dikenal: {action}"

        return None

    @staticmethod
    def _validate_role(
        role: Optional[str],
    ) -> Optional[str]:

        if role is None:
            return None

        if not isinstance(role, str):
            return "role harus string."

        if role not in ExecutionEngine.ROLE_ACTIONS:
            return f"role tidak dikenal: {role}"

        return None

    # ========================================================
    # STATE TRANSITION
    # ========================================================

    def transition(
        self,
        *,
        action: str,
        role: Optional[str] = None,
        reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExecutionResult:

        metadata = dict(
            metadata
            if metadata is not None
            else {}
        )

        previous_state = self.state

        # ----------------------------------------------------
        # ACTION VALIDATION
        # ----------------------------------------------------

        error = self._validate_action(
            action
        )

        if error:
            self._event(
                action=action,
                role=role,
                previous_state=previous_state,
                next_state=previous_state,
                accepted=False,
                reason=error,
            )

            return ExecutionResult(
                accepted=False,
                state=previous_state,
                action=action,
                role=role,
                reason=error,
                metadata=metadata,
            )

        # ----------------------------------------------------
        # ROLE VALIDATION
        # ----------------------------------------------------

        error = self._validate_role(
            role
        )

        if error:
            self._event(
                action=action,
                role=role,
                previous_state=previous_state,
                next_state=previous_state,
                accepted=False,
                reason=error,
            )

            return ExecutionResult(
                accepted=False,
                state=previous_state,
                action=action,
                role=role,
                reason=error,
                metadata=metadata,
            )

        # ----------------------------------------------------
        # TERMINAL STATE
        # ----------------------------------------------------

        if self.state in {
            STATE_FINISHED,
            STATE_ABORTED,
        }:

            error = (
                f"execution sudah terminal: "
                f"{self.state}"
            )

            self._event(
                action=action,
                role=role,
                previous_state=previous_state,
                next_state=previous_state,
                accepted=False,
                reason=error,
            )

            return ExecutionResult(
                accepted=False,
                state=previous_state,
                action=action,
                role=role,
                reason=error,
                metadata=metadata,
            )

        # ----------------------------------------------------
        # ROLE / ACTION COMPATIBILITY
        # ----------------------------------------------------

        if role is not None:

            allowed_actions = self.ROLE_ACTIONS.get(
                role,
                set(),
            )

            if action not in allowed_actions:

                error = (
                    f"action {action} tidak "
                    f"diizinkan untuk role {role}"
                )

                self._event(
                    action=action,
                    role=role,
                    previous_state=previous_state,
                    next_state=previous_state,
                    accepted=False,
                    reason=error,
                )

                return ExecutionResult(
                    accepted=False,
                    state=previous_state,
                    action=action,
                    role=role,
                    reason=error,
                    metadata=metadata,
                )

        # ----------------------------------------------------
        # ACTION -> STATE
        # ----------------------------------------------------

        next_state = self._next_state(
            action
        )

        self.state = next_state

        self._event(
            action=action,
            role=role,
            previous_state=previous_state,
            next_state=next_state,
            accepted=True,
            reason=reason or "ok",
        )

        return ExecutionResult(
            accepted=True,
            state=next_state,
            action=action,
            role=role,
            reason=reason or "ok",
            metadata=metadata,
        )

    # ========================================================
    # NEXT STATE
    # ========================================================

    @staticmethod
    def _next_state(
        action: str,
    ) -> str:

        if action == ACTION_FINISH:
            return STATE_FINISHED

        if action == ACTION_ABORT:
            return STATE_ABORTED

        if action in {
            ACTION_PLAN,
            ACTION_CODE,
            ACTION_REVIEW,
            ACTION_TEST,
            ACTION_DEBUG,
            ACTION_REPAIR,
        }:
            return STATE_RUNNING

        return STATE_RUNNING

    # ========================================================
    # DECISION INTEGRATION
    # ========================================================

    def apply_decision(
        self,
        decision: Any,
    ) -> ExecutionResult:
        """
        Menerima object Decision Engine.

        Decision harus menyediakan:
            action
            next_role
            reason
            metadata
        """

        if decision is None:
            return self.transition(
                action=ACTION_ABORT,
                reason="decision kosong.",
            )

        action = getattr(
            decision,
            "action",
            None,
        )

        role = getattr(
            decision,
            "next_role",
            None,
        )

        reason = getattr(
            decision,
            "reason",
            "",
        )

        metadata = getattr(
            decision,
            "metadata",
            {},
        )

        if not isinstance(
            metadata,
            dict,
        ):
            metadata = {}

        return self.transition(
            action=action,
            role=role,
            reason=reason,
            metadata=metadata,
        )

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict[str, Any]:
        """
        Status aman.

        Tidak mengandung secret.
        """

        return {
            "state": self.state,
            "history_length": len(
                self.history
            ),
            "last_event": (
                self.history[-1].to_dict()
                if self.history
                else None
            ),
        }

    # ========================================================
    # HISTORY
    # ========================================================

    def history_dicts(
        self,
    ) -> list[dict[str, Any]]:

        return [
            event.to_dict()
            for event in self.history
        ]

    # ========================================================
    # RESET
    # ========================================================

    def reset(self) -> None:
        """
        Reset execution state.

        History sengaja tetap dipertahankan
        agar audit tidak hilang.
        """

        self.state = STATE_IDLE

        self._event(
            action="reset",
            role=None,
            previous_state=STATE_FINISHED,
            next_state=STATE_IDLE,
            accepted=True,
            reason="execution reset.",
        )


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "ACTION_PLAN",
    "ACTION_CODE",
    "ACTION_REVIEW",
    "ACTION_TEST",
    "ACTION_DEBUG",
    "ACTION_REPAIR",
    "ACTION_FINISH",
    "ACTION_ABORT",
    "VALID_ACTIONS",
    "STATE_IDLE",
    "STATE_RUNNING",
    "STATE_WAITING",
    "STATE_FINISHED",
    "STATE_ABORTED",
    "ExecutionResult",
    "ExecutionEvent",
    "ExecutionEngine",
]
