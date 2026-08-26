from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoutingDecision:
    role: str
    confidence: float
    reason: str


class IntelligenceRouter:
    """
    Router sederhana untuk menentukan role awal.

    Nantinya router ini dapat ditingkatkan menjadi
    LLM-based planner/router.
    """

    RULES = {
        "debugger": (
            "error",
            "bug",
            "crash",
            "exception",
            "traceback",
            "gagal",
            "rusak",
            "debug",
        ),
        "tester": (
            "test",
            "testing",
            "pengujian",
            "coverage",
            "regression",
        ),
        "reviewer": (
            "review",
            "audit",
            "security",
            "aman",
            "periksa kode",
        ),
        "planner": (
            "rancang",
            "arsitektur",
            "blueprint",
            "plan",
            "design",
            "desain",
        ),
        "coder": (
            "buat",
            "implement",
            "tambahkan",
            "ubah",
            "fix",
            "coding",
            "kode",
        ),
    }

    def route(self, task: str) -> RoutingDecision:
        normalized = task.lower()

        scores = {
            role: 0
            for role in self.RULES
        }

        for role, keywords in self.RULES.items():
            for keyword in keywords:
                if keyword in normalized:
                    scores[role] += 1

        best_role = max(
            scores,
            key=scores.get,
        )

        best_score = scores[best_role]

        if best_score == 0:
            return RoutingDecision(
                role="planner",
                confidence=0.50,
                reason="Tidak ada intent kuat; mulai dari planner.",
            )

        total = sum(scores.values())

        confidence = min(
            0.99,
            0.50 + (
                best_score / max(total, 1)
            ) * 0.49,
        )

        return RoutingDecision(
            role=best_role,
            confidence=round(confidence, 3),
            reason=(
                f"Intent cocok dengan role "
                f"{best_role}."
            ),
        )
