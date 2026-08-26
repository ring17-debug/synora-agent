"""
Synora Decision Engine V1.

Tugas:
- menentukan langkah agent berikutnya;
- menggunakan routing, pipeline, verification, dan memory;
- menghasilkan keputusan terstruktur;
- tidak mengeksekusi perubahan kode secara langsung;
- aman digunakan dengan satu Gemini API key.

Decision Engine adalah otak pengambil keputusan.
Execution tetap dilakukan oleh AgentOrchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ============================================================
# DECISION
# ============================================================

@dataclass(frozen=True)
class AgentDecision:
    """
    Keputusan yang dihasilkan Decision Engine.

    action:
        Action utama yang harus dilakukan.

    next_role:
        Role agent berikutnya.

    should_continue:
        Apakah pipeline harus dilanjutkan.

    confidence:
        Confidence keputusan.

    reason:
        Alasan keputusan.

    metadata:
        Informasi tambahan yang aman untuk debugging/logging.
    """

    action: str
    next_role: Optional[str]
    should_continue: bool
    confidence: float
    reason: str
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialisasi keputusan ke dictionary aman.
        """

        return {
            "action": self.action,
            "next_role": self.next_role,
            "should_continue": self.should_continue,
            "confidence": self.confidence,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


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
# ENGINE
# ============================================================

class DecisionEngine:
    """
    Decision Engine V1.

    Engine ini bersifat deterministic terlebih dahulu.

    Alasan:
    - mudah diuji;
    - predictable;
    - tidak bergantung pada Gemini;
    - aman sebagai guard sebelum autonomous execution.

    Gemini nantinya dapat digunakan sebagai reasoning layer,
    tetapi hasil akhirnya tetap harus melewati policy engine ini.
    """

    MAX_REPAIR_ROUNDS = 2

    ROLE_TO_ACTION = {
        "planner": ACTION_PLAN,
        "coder": ACTION_CODE,
        "reviewer": ACTION_REVIEW,
        "tester": ACTION_TEST,
        "debugger": ACTION_DEBUG,
    }

    ACTION_TO_ROLE = {
        ACTION_PLAN: "planner",
        ACTION_CODE: "coder",
        ACTION_REVIEW: "reviewer",
        ACTION_TEST: "tester",
        ACTION_DEBUG: "debugger",
    }

    # --------------------------------------------------------
    # INIT
    # --------------------------------------------------------

    def __init__(
        self,
        *,
        max_repair_rounds: int = MAX_REPAIR_ROUNDS,
    ) -> None:

        if max_repair_rounds < 0:
            raise ValueError(
                "max_repair_rounds harus >= 0."
            )

        self.max_repair_rounds = (
            max_repair_rounds
        )

    # --------------------------------------------------------
    # NORMALIZATION
    # --------------------------------------------------------

    @staticmethod
    def _normalize_role(
        role: Optional[str],
    ) -> Optional[str]:

        if role is None:
            return None

        if not isinstance(role, str):
            return None

        role = role.strip().lower()

        if not role:
            return None

        return role

    @staticmethod
    def _normalize_confidence(
        value: Any,
    ) -> float:

        try:
            confidence = float(value)
        except (
            TypeError,
            ValueError,
        ):
            confidence = 0.50

        return max(
            0.0,
            min(
                0.99,
                confidence,
            ),
        )

    @staticmethod
    def _verification_failed(
        verification: Any,
    ) -> bool:

        if verification is None:
            return False

        if isinstance(
            verification,
            bool,
        ):
            return not verification

        if isinstance(
            verification,
            dict,
        ):
            if "valid" in verification:
                return not bool(
                    verification["valid"]
                )

            if "success" in verification:
                return not bool(
                    verification["success"]
                )

            if "passed" in verification:
                return not bool(
                    verification["passed"]
                )

        return False

    @staticmethod
    def _has_changes(
        changes: Any,
    ) -> bool:

        if changes is None:
            return False

        if isinstance(
            changes,
            bool,
        ):
            return changes

        if isinstance(
            changes,
            dict,
        ):
            return bool(changes)

        if isinstance(
            changes,
            (list, tuple, set),
        ):
            return len(changes) > 0

        return True

    # --------------------------------------------------------
    # BASIC DECISION
    # --------------------------------------------------------

    def decide(
        self,
        *,
        task: str,
        current_role: Optional[str] = None,
        current_status: str = "pending",
        verification: Any = None,
        changes: Any = None,
        repair_round: int = 0,
        pipeline_complete: bool = False,
        reviewer_approved: bool = False,
        tester_approved: bool = False,
    ) -> AgentDecision:
        """
        Menghasilkan keputusan berikutnya.

        Priority:

        1. invalid task -> abort
        2. pipeline complete -> finish
        3. verification failure -> debug/repair
        4. reviewer rejection -> coder
        5. tester rejection -> debugger
        6. role transition
        7. fallback -> planner
        """

        # ----------------------------------------------------
        # TASK VALIDATION
        # ----------------------------------------------------

        if not isinstance(task, str):
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=0.99,
                reason=(
                    "Task tidak valid: "
                    "task harus berupa string."
                ),
            )

        if not task.strip():
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=0.99,
                reason=(
                    "Task kosong; "
                    "pipeline dihentikan."
                ),
            )

        # ----------------------------------------------------
        # REPAIR LIMIT
        # ----------------------------------------------------

        if repair_round > self.max_repair_rounds:
            return AgentDecision(
                action=ACTION_ABORT,
                next_role=None,
                should_continue=False,
                confidence=0.99,
                reason=(
                    "Batas repair round tercapai; "
                    "pipeline dihentikan untuk mencegah "
                    "loop tanpa akhir."
                ),
                metadata={
                    "repair_round": repair_round,
                    "max_repair_rounds": (
                        self.max_repair_rounds
                    ),
                },
            )

        # ----------------------------------------------------
        # COMPLETION
        # ----------------------------------------------------

        if pipeline_complete:
            return AgentDecision(
                action=ACTION_FINISH,
                next_role=None,
                should_continue=False,
                confidence=0.99,
                reason=(
                    "Pipeline sudah selesai."
                ),
            )

        # ----------------------------------------------------
        # VERIFICATION FAILURE
        # ----------------------------------------------------

        if self._verification_failed(
            verification
        ):

            if repair_round >= self.max_repair_rounds:
                return AgentDecision(
                    action=ACTION_ABORT,
                    next_role=None,
                    should_continue=False,
                    confidence=0.99,
                    reason=(
                        "Verification gagal dan "
                        "batas repair round tercapai."
                    ),
                    metadata={
                        "repair_round": repair_round,
                        "max_repair_rounds": (
                            self.max_repair_rounds
                        ),
                    },
                )

            return AgentDecision(
                action=ACTION_DEBUG,
                next_role="debugger",
                should_continue=True,
                confidence=0.98,
                reason=(
                    "Verification gagal; "
                    "debugger diperlukan untuk "
                    "mencari root cause."
                ),
                metadata={
                    "repair_round": repair_round,
                    "repair_required": True,
                },
            )

        # ----------------------------------------------------
        # REVIEWER
        # ----------------------------------------------------

        if (
            current_role == "reviewer"
            and not reviewer_approved
        ):
            return AgentDecision(
                action=ACTION_REPAIR,
                next_role="coder",
                should_continue=True,
                confidence=0.97,
                reason=(
                    "Reviewer menolak perubahan; "
                    "coder harus memperbaiki."
                ),
                metadata={
                    "repair_round": repair_round + 1,
                    "reviewer_approved": False,
                },
            )

        # ----------------------------------------------------
        # TESTER
        # ----------------------------------------------------

        if (
            current_role == "tester"
            and not tester_approved
        ):
            return AgentDecision(
                action=ACTION_DEBUG,
                next_role="debugger",
                should_continue=True,
                confidence=0.97,
                reason=(
                    "Tester menemukan masalah; "
                    "debugger diperlukan."
                ),
                metadata={
                    "repair_round": repair_round + 1,
                    "tester_approved": False,
                },
            )

        # ----------------------------------------------------
        # CURRENT ROLE TRANSITION
        # ----------------------------------------------------

        normalized_role = self._normalize_role(
            current_role
        )

        if normalized_role == "planner":
            return AgentDecision(
                action=ACTION_CODE,
                next_role="coder",
                should_continue=True,
                confidence=0.96,
                reason=(
                    "Planning selesai; "
                    "lanjut ke coder."
                ),
            )

        if normalized_role == "coder":
            return AgentDecision(
                action=ACTION_REVIEW,
                next_role="reviewer",
                should_continue=True,
                confidence=0.96,
                reason=(
                    "Implementasi selesai; "
                    "lanjut ke reviewer."
                ),
                metadata={
                    "has_changes": self._has_changes(
                        changes
                    ),
                },
            )

        if normalized_role == "reviewer":
            return AgentDecision(
                action=ACTION_TEST,
                next_role="tester",
                should_continue=True,
                confidence=0.96,
                reason=(
                    "Review diterima; "
                    "lanjut ke tester."
                ),
            )

        if normalized_role == "tester":
            return AgentDecision(
                action=ACTION_FINISH,
                next_role=None,
                should_continue=False,
                confidence=0.98,
                reason=(
                    "Testing selesai dan "
                    "tidak ada failure."
                ),
            )

        if normalized_role == "debugger":
            return AgentDecision(
                action=ACTION_REPAIR,
                next_role="coder",
                should_continue=True,
                confidence=0.95,
                reason=(
                    "Root cause ditemukan; "
                    "coder menjalankan repair."
                ),
                metadata={
                    "repair_round": repair_round + 1,
                },
            )

        # ----------------------------------------------------
        # STATUS-BASED FALLBACK
        # ----------------------------------------------------

        if current_status in {
            "failed",
            "error",
            "failure",
        }:
            return AgentDecision(
                action=ACTION_DEBUG,
                next_role="debugger",
                should_continue=True,
                confidence=0.95,
                reason=(
                    "Status agent menunjukkan failure; "
                    "debugger dipilih."
                ),
            )

        # ----------------------------------------------------
        # DEFAULT
        # ----------------------------------------------------

        return AgentDecision(
            action=ACTION_PLAN,
            next_role="planner",
            should_continue=True,
            confidence=0.90,
            reason=(
                "Tidak ada keputusan spesifik; "
                "mulai dari planner."
            ),
        )

    # --------------------------------------------------------
    # ROUTING DECISION
    # --------------------------------------------------------

    def decide_from_routing(
        self,
        task: str,
        routing_decision: Any,
    ) -> AgentDecision:
        """
        Mengubah RoutingDecision menjadi keputusan
        awal execution.
        """

        role = self._normalize_role(
            getattr(
                routing_decision,
                "role",
                None,
            )
        )

        confidence = self._normalize_confidence(
            getattr(
                routing_decision,
                "confidence",
                0.50,
            )
        )

        reason = getattr(
            routing_decision,
            "reason",
            "Routing selesai.",
        )

        if role not in self.ROLE_TO_ACTION:
            return AgentDecision(
                action=ACTION_PLAN,
                next_role="planner",
                should_continue=True,
                confidence=0.80,
                reason=(
                    "Role routing tidak dikenal; "
                    "fallback ke planner."
                ),
                metadata={
                    "original_role": role,
                },
            )

        # Untuk task coding, tetap mulai dari planner.
        #
        # Router menentukan domain utama.
        # Decision Engine menentukan execution flow.
        if role == "coder":
            return AgentDecision(
                action=ACTION_PLAN,
                next_role="planner",
                should_continue=True,
                confidence=confidence,
                reason=(
                    f"{reason} "
                    "Execution dimulai dari planner."
                ),
                metadata={
                    "routed_role": role,
                },
            )

        if role == "debugger":
            return AgentDecision(
                action=ACTION_DEBUG,
                next_role="debugger",
                should_continue=True,
                confidence=confidence,
                reason=(
                    f"{reason} "
                    "Debugger menjadi entry point."
                ),
                metadata={
                    "routed_role": role,
                },
            )

        if role == "reviewer":
            return AgentDecision(
                action=ACTION_REVIEW,
                next_role="reviewer",
                should_continue=True,
                confidence=confidence,
                reason=(
                    f"{reason} "
                    "Reviewer menjadi entry point."
                ),
                metadata={
                    "routed_role": role,
                },
            )

        if role == "tester":
            return AgentDecision(
                action=ACTION_TEST,
                next_role="tester",
                should_continue=True,
                confidence=confidence,
                reason=(
                    f"{reason} "
                    "Tester menjadi entry point."
                ),
                metadata={
                    "routed_role": role,
                },
            )

        return AgentDecision(
            action=ACTION_PLAN,
            next_role="planner",
            should_continue=True,
            confidence=confidence,
            reason=(
                f"{reason} "
                "Planner menjadi entry point."
            ),
            metadata={
                "routed_role": role,
            },
        )

    # --------------------------------------------------------
    # PIPELINE DECISION
    # --------------------------------------------------------

    def decide_pipeline(
        self,
        pipeline: Iterable[Any],
        *,
        index: int = 0,
        verification: Any = None,
        repair_round: int = 0,
    ) -> AgentDecision:
        """
        Menentukan langkah berikutnya berdasarkan pipeline.
        """

        items = list(pipeline)

        if index < 0:
            index = 0

        if index >= len(items):
            return AgentDecision(
                action=ACTION_FINISH,
                next_role=None,
                should_continue=False,
                confidence=0.99,
                reason=(
                    "Semua pipeline step telah selesai."
                ),
            )

        if self._verification_failed(
            verification
        ):
            return self.decide(
                task="pipeline verification",
                current_role=getattr(
                    items[index],
                    "role",
                    None,
                ),
                verification=verification,
                repair_round=repair_round,
            )

        item = items[index]

        role = self._normalize_role(
            getattr(
                item,
                "role",
                None,
            )
        )

        action = self.ROLE_TO_ACTION.get(
            role,
            ACTION_PLAN,
        )

        return AgentDecision(
            action=action,
            next_role=role,
            should_continue=True,
            confidence=0.95,
            reason=(
                f"Pipeline step {index + 1} "
                f"menggunakan role {role}."
            ),
            metadata={
                "pipeline_index": index,
                "pipeline_size": len(items),
            },
        )


# ============================================================
# FACTORY
# ============================================================

def create_decision_engine(
    *,
    max_repair_rounds: int = (
        DecisionEngine.MAX_REPAIR_ROUNDS
    ),
) -> DecisionEngine:
    """
    Factory Decision Engine.
    """

    return DecisionEngine(
        max_repair_rounds=max_repair_rounds,
    )


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "AgentDecision",
    "DecisionEngine",
    "create_decision_engine",
    "ACTION_PLAN",
    "ACTION_CODE",
    "ACTION_REVIEW",
    "ACTION_TEST",
    "ACTION_DEBUG",
    "ACTION_REPAIR",
    "ACTION_FINISH",
    "ACTION_ABORT",
    "VALID_ACTIONS",
]
