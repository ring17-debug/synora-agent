"""
Synora Verification Engine V1.

Verification layer untuk execution pipeline.

Tanggung jawab:
- membaca verification contract dari ExecutionState
- menjalankan verification checks yang terdaftar
- mengumpulkan evidence
- menghasilkan VerificationResult terstruktur
- menentukan apakah execution dapat dianggap selesai
- tidak menyimpan credential/API key
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ============================================================
# CONSTANTS
# ============================================================

VERIFICATION_PENDING = "pending"
VERIFICATION_PASSED = "passed"
VERIFICATION_FAILED = "failed"
VERIFICATION_SKIPPED = "skipped"


# ============================================================
# TYPES
# ============================================================

VerificationCheck = Callable[
    [Any],
    Any,
]


# ============================================================
# RESULT
# ============================================================

@dataclass(frozen=True)
class VerificationResult:
    """
    Hasil verification pipeline.

    Representasi internal menggunakan tuple agar immutable.
    Ketika diekspor melalui to_dict(), collection dikonversi
    menjadi list agar sesuai dengan ExecutionState contract
    dan struktur JSON-friendly.
    """

    status: str

    passed: bool

    required: bool = True

    checks: tuple[dict[str, Any], ...] = ()

    evidence: tuple[Any, ...] = ()

    errors: tuple[str, ...] = ()

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert verification result menjadi public state contract.

        Collection sengaja dikonversi dari tuple -> list.

        Ini penting karena state.verification merupakan data
        runtime/public contract yang harus mudah dikonsumsi oleh:

        - repair engine
        - execution history
        - context builder
        - JSON serialization
        - test/integration layer
        """

        return {
            "status": self.status,
            "passed": self.passed,
            "required": self.required,
            "checks": [
                dict(check)
                if isinstance(check, dict)
                else check
                for check in self.checks
            ],
            "evidence": list(
                self.evidence
            ),
            "errors": list(
                self.errors
            ),
            "metadata": dict(
                self.metadata
            ),
        }


# ============================================================
# ENGINE
# ============================================================

class VerificationEngine:
    """
    Menjalankan verification checks terhadap execution state.

    Check menerima satu argument:

        check(state)

    Check dapat mengembalikan:

        True
        False

    atau:

        {
            "passed": True,
            "name": "unit_tests",
            "evidence": "...",
        }

    Jika check mengembalikan dictionary, field berikut
    akan diproses secara khusus:

        passed
        name
        evidence
        error
        metadata
    """

    def __init__(self) -> None:
        self.checks: dict[
            str,
            VerificationCheck,
        ] = {}

    # ========================================================
    # REGISTER
    # ========================================================

    def register(
        self,
        name: str,
        check: VerificationCheck,
    ) -> None:
        """
        Register verification check.
        """

        if not isinstance(name, str):
            raise TypeError(
                "name harus string."
            )

        normalized = name.strip()

        if not normalized:
            raise ValueError(
                "name tidak boleh kosong."
            )

        if not callable(check):
            raise TypeError(
                "check harus callable."
            )

        self.checks[normalized] = check

    # ========================================================
    # UNREGISTER
    # ========================================================

    def unregister(
        self,
        name: str,
    ) -> bool:
        """
        Hapus verification check.
        """

        if not isinstance(name, str):
            raise TypeError(
                "name harus string."
            )

        normalized = name.strip()

        if not normalized:
            raise ValueError(
                "name tidak boleh kosong."
            )

        return (
            self.checks.pop(
                normalized,
                None,
            )
            is not None
        )

    # ========================================================
    # HAS
    # ========================================================

    def has_check(
        self,
        name: str,
    ) -> bool:
        """
        Mengecek apakah verification check tersedia.
        """

        if not isinstance(name, str):
            return False

        return name.strip() in self.checks

    # ========================================================
    # NORMALIZE RESULT
    # ========================================================

    @staticmethod
    def _normalize_check_result(
        name: str,
        result: Any,
    ) -> dict[str, Any]:
        """
        Normalisasi hasil check menjadi dictionary.
        """

        if isinstance(result, bool):
            return {
                "name": name,
                "passed": result,
            }

        if isinstance(result, dict):
            data = dict(result)

            data.setdefault(
                "name",
                name,
            )

            data.setdefault(
                "passed",
                False,
            )

            return data

        return {
            "name": name,
            "passed": bool(result),
        }

    # ========================================================
    # REQUIRED
    # ========================================================

    @staticmethod
    def _required(
        state: Any,
    ) -> bool:
        """
        Membaca verification.required.

        Default True jika tidak ditentukan.
        """

        verification = getattr(
            state,
            "verification",
            {},
        )

        if not isinstance(
            verification,
            dict,
        ):
            return True

        value = verification.get(
            "required",
            True,
        )

        if isinstance(
            value,
            bool,
        ):
            return value

        return bool(value)

    # ========================================================
    # VERIFY
    # ========================================================

    def verify(
        self,
        state: Any,
    ) -> VerificationResult:
        """
        Menjalankan seluruh verification checks.

        Jika tidak ada check terdaftar:

        - required=True  -> pending
        - required=False -> skipped

        Ini mencegah engine mengklaim verification
        berhasil padahal verification belum dilakukan.
        """

        required = self._required(
            state
        )

        # ----------------------------------------------------
        # NO CHECKS
        # ----------------------------------------------------

        if not self.checks:

            if required:
                return VerificationResult(
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

            return VerificationResult(
                status=VERIFICATION_SKIPPED,
                passed=True,
                required=False,
                checks=(),
                evidence=(),
                errors=(),
                metadata={
                    "checks_registered": 0,
                    "verification_performed": False,
                },
            )

        # ----------------------------------------------------
        # RUN CHECKS
        # ----------------------------------------------------

        check_results: list[
            dict[str, Any]
        ] = []

        evidence: list[Any] = []

        errors: list[str] = []

        all_passed = True

        for name, check in self.checks.items():

            try:
                raw_result = check(
                    state
                )

                result = self._normalize_check_result(
                    name,
                    raw_result,
                )

            except Exception as error:
                result = {
                    "name": name,
                    "passed": False,
                    "error": str(error),
                }

            passed = result.get(
                "passed",
                False,
            )

            if not isinstance(
                passed,
                bool,
            ):
                passed = bool(passed)

            result["passed"] = passed

            check_results.append(
                result
            )

            if not passed:
                all_passed = False

            if "evidence" in result:
                evidence.append(
                    result["evidence"]
                )

            error = result.get(
                "error"
            )

            if error:
                errors.append(
                    str(error)
                )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        if all_passed:
            status = VERIFICATION_PASSED
        else:
            status = VERIFICATION_FAILED

        return VerificationResult(
            status=status,
            passed=all_passed,
            required=required,
            checks=tuple(
                check_results
            ),
            evidence=tuple(
                evidence
            ),
            errors=tuple(
                errors
            ),
            metadata={
                "checks_registered": len(
                    self.checks
                ),
                "checks_executed": len(
                    check_results
                ),
                "verification_performed": True,
            },
        )


# ============================================================
# DEFAULT CHECKS
# ============================================================

def create_verification_engine() -> VerificationEngine:
    """
    Membuat VerificationEngine kosong.

    Check harus diregister oleh caller karena engine
    tidak boleh mengarang cara verification project.
    """

    return VerificationEngine()


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "VerificationCheck",
    "VerificationResult",
    "VerificationEngine",
    "VERIFICATION_PENDING",
    "VERIFICATION_PASSED",
    "VERIFICATION_FAILED",
    "VERIFICATION_SKIPPED",
    "create_verification_engine",
]
