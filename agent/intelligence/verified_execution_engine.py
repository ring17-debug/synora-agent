"""
Synora Verified Execution Engine.

Composition layer yang menghubungkan ExecutionEngineV2
dengan VerificationEngine tanpa mengubah behavior legacy
ExecutionEngineV2.

Alur:

    ExecutionEngineV2
            ↓
       role pipeline
            ↓
    VerificationEngine
            ↓
       PASS / FAIL

Prinsip:
- tidak menyimpan credential/API key
- tidak mengubah ExecutionEngineV2 secara langsung
- backward-compatible ketika tidak ada verification check
- verification result disimpan pada ExecutionState
- verification failure membuat execution failed
"""

from __future__ import annotations

from typing import Any

from .execution_engine_v2 import (
    ExecutionEngineV2,
    ExecutionState,
    STATUS_FAILED,
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
    ExecutionEngineV2 yang dilengkapi verification stage.

    ExecutionEngineV2 tetap menjadi engine utama untuk menjalankan
    role. Class ini hanya menambahkan verification setelah pipeline
    selesai.

    Verification hanya dijalankan apabila terdapat verification
    check yang terdaftar.

    Ini penting untuk backward compatibility karena execution
    pipeline lama belum tentu memiliki verification check.
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

        Struktur state.verification menjadi:

            {
                ...contract lama...,
                "status": "passed",
                "passed": True,
                "required": True,
                "checks": [...],
                "evidence": [...],
                "errors": [...],
                "metadata": {...},
            }
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

        verification.update(
            result.to_dict()
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

        Behavior:

        1. Jalankan ExecutionEngineV2.
        2. Jika execution gagal, langsung return.
        3. Jika tidak ada verification check, pertahankan
           behavior lama.
        4. Jika check tersedia dan verify=True, jalankan
           VerificationEngine.
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
    # EXECUTE AND REQUIRE VERIFICATION
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

        Jika tidak ada check terdaftar, execution dianggap gagal
        karena caller secara eksplisit meminta verified execution.
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
