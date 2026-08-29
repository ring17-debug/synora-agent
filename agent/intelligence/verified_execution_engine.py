"""
Synora Verified Execution Engine.

Composition layer yang menghubungkan ExecutionEngineV2
dengan VerificationEngine.

Alur utama:

    ExecutionEngineV2
            ↓
       role pipeline
            ↓
    VerificationEngine
            ↓
       PASS / FAIL
            ↓
      Repair Loop
            ↓
       Re-verification

Prinsip:
- tidak menyimpan credential/API key
- tidak mengubah behavior legacy ExecutionEngineV2
- verification result disimpan pada ExecutionState
- verification failure membuat execution failed
- repair dilakukan hanya jika verification gagal
- repair dibatasi max_repair_rounds
- repair role menerima verification failure context
"""

from __future__ import annotations

from typing import Any

from .execution_engine_v2 import (
    ExecutionEngineV2,
    ExecutionState,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
)

from .verification_engine import (
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    VERIFICATION_PENDING,
    VERIFICATION_SKIPPED,
    VerificationEngine,
    VerificationResult,
    create_verification_engine,
)


class VerifiedExecutionEngine:
    """
    ExecutionEngineV2 yang dilengkapi verification stage
    dan controlled repair loop.
    """

    def __init__(
        self,
        execution_engine: ExecutionEngineV2 | None = None,
        verification_engine: VerificationEngine | None = None,
    ) -> None:
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

        self.execution_engine = (
            execution_engine
            if execution_engine is not None
            else ExecutionEngineV2()
        )

        self.verification_engine = (
            verification_engine
            if verification_engine is not None
            else create_verification_engine()
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

        self.verification_engine.register(
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
        Remove verification check.
        """

        return self.verification_engine.unregister(
            name
        )

    # ========================================================
    # HAS VERIFICATION
    # ========================================================

    def has_verification(
        self,
        name: str,
    ) -> bool:
        """
        Mengecek apakah verification check tersedia.
        """

        return self.verification_engine.has_check(
            name
        )

    # ========================================================
    # VERIFY
    # ========================================================

    def verify(
        self,
        state: ExecutionState,
    ) -> VerificationResult:
        """
        Jalankan verification terhadap state.
        """

        if not isinstance(
            state,
            ExecutionState,
        ):
            raise TypeError(
                "state harus ExecutionState."
            )

        result = self.verification_engine.verify(
            state
        )

        self._apply_verification(
            state,
            result,
        )

        return result

    # ========================================================
    # APPLY VERIFICATION
    # ========================================================

    @staticmethod
    def _apply_verification(
        state: ExecutionState,
        result: VerificationResult,
    ) -> None:
        """
        Simpan hasil verification ke ExecutionState.

        Collection field dinormalisasi menjadi list agar
        konsisten untuk repair role, JSON, RPC, logging,
        dan test.
        """

        existing = state.verification

        if not isinstance(
            existing,
            dict,
        ):
            existing = {}

        verification = dict(
            existing
        )

        result_data = result.to_dict()

        for key in (
            "checks",
            "evidence",
            "errors",
        ):
            value = result_data.get(
                key
            )

            if isinstance(
                value,
                tuple,
            ):
                result_data[key] = list(
                    value
                )
            elif isinstance(
                value,
                list,
            ):
                result_data[key] = list(
                    value
                )

        verification.update(
            result_data
        )

        state.verification = verification

        state.metadata[
            "verification_status"
        ] = result.status

        state.metadata[
            "verification_passed"
        ] = result.passed

        state.metadata[
            "verification_performed"
        ] = result.metadata.get(
            "verification_performed",
            False,
        )

        state.add_history(
            "verification_completed",
            status=result.status,
            passed=result.passed,
            required=result.required,
            checks=len(result.checks),
        )

        if (
            result.required
            and not result.passed
        ):
            state.status = STATUS_FAILED

            state.add_history(
                "execution_failed_verification",
                status=result.status,
                errors=list(
                    result.errors
                ),
            )

    # ========================================================
    # BUILD REPAIR CONTEXT
    # ========================================================

    @staticmethod
    def _build_repair_context(
        state: ExecutionState,
    ) -> str:
        """
        Membuat context khusus untuk repair role.
        """

        verification = state.verification

        if not isinstance(
            verification,
            dict,
        ):
            verification = {}

        errors = verification.get(
            "errors",
            [],
        )

        evidence = verification.get(
            "evidence",
            [],
        )

        if isinstance(
            errors,
            tuple,
        ):
            errors = list(errors)

        if isinstance(
            evidence,
            tuple,
        ):
            evidence = list(evidence)

        lines = [
            "REPAIR REQUIRED",
            "",
            f"Task: {state.task}",
            "",
            "Current execution status:",
            state.status,
            "",
            "Current plan:",
            state.plan,
            "",
            "Current changes:",
            str(state.changes),
            "",
            "Verification status:",
            str(
                verification.get(
                    "status",
                    VERIFICATION_FAILED,
                )
            ),
            "",
            "Verification errors:",
            str(errors),
            "",
            "Verification evidence:",
            str(evidence),
            "",
        ]

        if state.context:
            lines.extend(
                [
                    "Previous context:",
                    state.context,
                    "",
                ]
            )

        lines.extend(
            [
                "Repair instruction:",
                (
                    "Perbaiki hasil execution berdasarkan "
                    "verification failure di atas."
                ),
            ]
        )

        return "\n".join(lines)

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(
        self,
        task: str,
        pipeline: list[str],
        *,
        context: str = "",
        repair_round: int = 0,
        verify: bool = True,
    ) -> ExecutionState:
        """
        Jalankan execution pipeline lalu verification.
        """

        state = self.execution_engine.execute(
            task,
            pipeline,
            context=context,
            repair_round=repair_round,
        )

        if state.status != STATUS_SUCCESS:
            return state

        if not verify:
            state.add_history(
                "verification_skipped",
                reason="disabled_by_caller",
            )

            return state

        if not self.verification_engine.checks:
            state.metadata[
                "verification_performed"
            ] = False

            state.metadata[
                "verification_status"
            ] = VERIFICATION_SKIPPED

            state.add_history(
                "verification_skipped",
                reason="no_checks_registered",
            )

            return state

        self.verify(
            state
        )

        return state

    # ========================================================
    # EXECUTE VERIFIED
    # ========================================================

    def execute_verified(
        self,
        task: str,
        pipeline: list[str],
        *,
        context: str = "",
        repair_round: int = 0,
    ) -> ExecutionState:
        """
        Jalankan pipeline dan wajib melakukan verification.
        """

        state = self.execution_engine.execute(
            task,
            pipeline,
            context=context,
            repair_round=repair_round,
        )

        if state.status != STATUS_SUCCESS:
            return state

        if not self.verification_engine.checks:
            result = VerificationResult(
                status=VERIFICATION_PENDING,
                passed=False,
                required=True,
                checks=(),
                evidence=(),
                errors=(
                    "Tidak ada verification check "
                    "yang terdaftar.",
                ),
                metadata={
                    "checks_registered": 0,
                    "verification_performed": False,
                },
            )

            self._apply_verification(
                state,
                result,
            )

            return state

        self.verify(
            state
        )

        return state

    # ========================================================
    # EXECUTE WITH REPAIR
    # ========================================================

    def execute_with_repair(
        self,
        task: str,
        pipeline: list[str],
        *,
        context: str = "",
        repair_role: str = "repairer",
        max_repair_rounds: int | None = None,
    ) -> ExecutionState:
        """
        Jalankan execution → verification → repair → re-verification.

        Repair hanya dijalankan apabila verification gagal.

        Lifecycle:

            execute
                ↓
            verify
                ↓
          PASS ─────────────→ success
                ↓
              FAIL
                ↓
        repair_round < limit?
             ↙       ↘
           no        yes
           ↓          ↓
       exhausted   repairer
                        ↓
                  verification_retried
                        ↓
                    re-verify
                        ↓
                  PASS / FAIL
                        ↓
                    repeat
        """

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not isinstance(
            repair_role,
            str,
        ):
            raise TypeError(
                "repair_role harus string."
            )

        normalized_repair_role = (
            repair_role.strip()
        )

        if not normalized_repair_role:
            raise ValueError(
                "repair_role tidak boleh kosong."
            )

        if max_repair_rounds is None:
            limit = (
                self.execution_engine.max_repair_rounds
            )
        else:
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

            limit = max_repair_rounds

        # ----------------------------------------------------
        # INITIAL EXECUTION
        # ----------------------------------------------------

        state = self.execution_engine.execute(
            task,
            pipeline,
            context=context,
            repair_round=0,
        )

        if state.status != STATUS_SUCCESS:
            return state

        # ----------------------------------------------------
        # NO VERIFICATION
        # ----------------------------------------------------

        if not self.verification_engine.checks:
            state.metadata[
                "verification_performed"
            ] = False

            state.metadata[
                "verification_status"
            ] = VERIFICATION_SKIPPED

            state.add_history(
                "verification_skipped",
                reason="no_checks_registered",
            )

            return state

        # ----------------------------------------------------
        # INITIAL VERIFICATION
        # ----------------------------------------------------

        result = self.verify(
            state
        )

        if (
            result.passed
            or not result.required
        ):
            state.status = STATUS_SUCCESS

            return state

        # ----------------------------------------------------
        # REPAIR DISABLED
        # ----------------------------------------------------

        if limit == 0:
            state.status = STATUS_FAILED

            state.add_history(
                "repair_exhausted",
                reason="max_repair_rounds",
                repair_round=state.repair_round,
                max_repair_rounds=limit,
            )

            return state

        # ----------------------------------------------------
        # REPAIR LOOP
        # ----------------------------------------------------

        while state.repair_round < limit:
            # ------------------------------------------------
            # ROLE AVAILABILITY
            # ------------------------------------------------

            if not self.execution_engine.has_handler(
                normalized_repair_role
            ):
                state.status = STATUS_FAILED

                state.add_history(
                    "repair_exhausted",
                    reason="repair_role_not_registered",
                    repair_role=normalized_repair_role,
                    repair_round=state.repair_round,
                    max_repair_rounds=limit,
                )

                return state

            next_round = (
                state.repair_round + 1
            )

            state.repair_round = next_round

            # ------------------------------------------------
            # BUILD REPAIR CONTEXT
            # ------------------------------------------------

            repair_context = (
                self._build_repair_context(
                    state
                )
            )

            state.context = repair_context

            state.add_history(
                "repair_started",
                repair_role=normalized_repair_role,
                repair_round=next_round,
            )

            # ------------------------------------------------
            # REPAIR ROLE
            # ------------------------------------------------

            repair_result = (
                self.execution_engine.execute_role(
                    state,
                    normalized_repair_role,
                )
            )

            if (
                repair_result.status
                != STATUS_SUCCESS
            ):
                state.status = STATUS_FAILED

                state.add_history(
                    "repair_failed",
                    repair_role=normalized_repair_role,
                    repair_round=next_round,
                    error=repair_result.error,
                )

                if state.repair_round >= limit:
                    state.add_history(
                        "repair_exhausted",
                        reason="repair_failed",
                        repair_round=state.repair_round,
                        max_repair_rounds=limit,
                    )

                return state

            state.add_history(
                "repair_completed",
                repair_role=normalized_repair_role,
                repair_round=next_round,
            )

            # ------------------------------------------------
            # PREPARE FOR RE-VERIFICATION
            # ------------------------------------------------

            state.status = STATUS_RUNNING

            state.add_history(
                "verification_retried",
                repair_round=next_round,
            )

            # ------------------------------------------------
            # RE-VERIFY
            # ------------------------------------------------

            result = self.verify(
                state
            )

            if (
                result.passed
                or not result.required
            ):
                state.status = STATUS_SUCCESS

                state.add_history(
                    "repair_loop_completed",
                    repair_round=state.repair_round,
                    verification_status=result.status,
                    verification_passed=result.passed,
                )

                return state

            # ------------------------------------------------
            # VERIFICATION STILL FAILED
            # ------------------------------------------------

            state.status = STATUS_FAILED

        # ----------------------------------------------------
        # EXHAUSTED
        # ----------------------------------------------------

        state.status = STATUS_FAILED

        state.add_history(
            "repair_exhausted",
            reason="max_repair_rounds",
            repair_round=state.repair_round,
            max_repair_rounds=limit,
        )

        return state


# ============================================================
# FACTORY
# ============================================================

def create_verified_execution_engine(
    *,
    execution_engine: ExecutionEngineV2 | None = None,
    verification_engine: VerificationEngine | None = None,
) -> VerifiedExecutionEngine:
    """
    Factory untuk VerifiedExecutionEngine.
    """

    return VerifiedExecutionEngine(
        execution_engine=execution_engine,
        verification_engine=verification_engine,
    )


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "VerifiedExecutionEngine",
    "create_verified_execution_engine",
]
