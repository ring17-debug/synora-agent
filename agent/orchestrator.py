#!/usr/bin/env python3

"""
Synora Agent Intelligence V3
Multi-Agent Orchestrator

Pipeline:

USER TASK
    ↓
PLANNER
    ↓
CODER
    ↓
REVIEWER
    ↓
TESTER
    ↓
FINAL VERIFIER
    ↓
DONE

Jika reviewer/tester menemukan masalah:

REVIEWER / TESTER
    ↓
DEBUGGER
    ↓
CODER
    ↓
REVIEWER
    ↓
TESTER

File ini adalah orchestration layer.
Transaction safety tetap berada di agent/main.py.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / ".synora-agent"


# ============================================================
# IMPORT INTELLIGENCE
# ============================================================

try:
    from .intelligence import (
        RoutingDecision,
        route_task,
        get_role_prompt,
    )
except ImportError:
    from intelligence import (
        RoutingDecision,
        route_task,
        get_role_prompt,
    )


# ============================================================
# CONFIG
# ============================================================

MAX_REPAIR_ROUNDS = 2

ROLE_ORDER = (
    "planner",
    "coder",
    "reviewer",
    "tester",
)

RECOVERY_ROLE = "debugger"


# ============================================================
# UTILITIES
# ============================================================

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def print_header(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def ensure_agent_dir() -> None:
    AGENT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


# ============================================================
# AGENT RESULT
# ============================================================

@dataclass
class AgentResult:
    role: str
    status: str
    output: str = ""
    confidence: float = 0.0
    reason: str = ""
    metadata: Dict[str, Any] = field(
        default_factory=dict
    )

    @property
    def success(self) -> bool:
        return self.status == "success"


# ============================================================
# ORCHESTRATION STATE
# ============================================================

@dataclass
class OrchestrationState:
    task: str

    route: Optional[RoutingDecision] = None

    plan: str = ""

    code_result: Optional[AgentResult] = None

    review_result: Optional[AgentResult] = None

    test_result: Optional[AgentResult] = None

    debug_result: Optional[AgentResult] = None

    repair_round: int = 0

    history: List[Dict[str, Any]] = field(
        default_factory=list
    )

    metadata: Dict[str, Any] = field(
        default_factory=dict
    )

    def record(
        self,
        result: AgentResult,
    ) -> None:
        self.history.append(
            {
                "timestamp": now(),
                "role": result.role,
                "status": result.status,
                "confidence": result.confidence,
                "reason": result.reason,
                "output": result.output,
            }
        )


# ============================================================
# ROLE EXECUTOR
# ============================================================

Executor = Callable[
    [str, OrchestrationState],
    AgentResult,
]


class AgentExecutor:
    """
    Abstraksi eksekusi agent.

    Untuk V3 awal, executor default hanya mensimulasikan
    agent pipeline. Nanti executor ini dapat dihubungkan
    langsung ke Gemini API.
    """

    def __init__(
        self,
        handlers: Optional[
            Dict[str, Executor]
        ] = None,
    ):
        self.handlers = handlers or {}

    def register(
        self,
        role: str,
        handler: Executor,
    ) -> None:
        self.handlers[role] = handler

    def run(
        self,
        role: str,
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        handler = self.handlers.get(role)

        if handler is None:
            return self._default_run(
                role,
                task,
                state,
            )

        try:
            return handler(
                task,
                state,
            )

        except Exception as error:
            return AgentResult(
                role=role,
                status="failed",
                reason=str(error),
            )

    def _default_run(
        self,
        role: str,
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        prompt = get_role_prompt(role)

        return AgentResult(
            role=role,
            status="success",
            confidence=0.90,
            reason=f"Role {role} berhasil dijalankan.",
            output=(
                f"[SIMULATED {role.upper()}]\n\n"
                f"Task:\n{task}\n\n"
                f"Role Prompt:\n{prompt}"
            ),
        )


# ============================================================
# ORCHESTRATOR
# ============================================================

class SynoraOrchestrator:
    """
    Mesin utama multi-agent.

    Tanggung jawab:

    1. Routing task.
    2. Menjalankan planner.
    3. Menjalankan coder.
    4. Menjalankan reviewer.
    5. Menjalankan tester.
    6. Jika gagal, menjalankan debugger.
    7. Membatasi repair loop.
    8. Menyimpan execution history.
    """

    def __init__(
        self,
        executor: Optional[AgentExecutor] = None,
    ):
        self.executor = (
            executor
            or AgentExecutor()
        )

    # --------------------------------------------------------
    # ROUTING
    # --------------------------------------------------------

    def route(
        self,
        task: str,
    ) -> RoutingDecision:

        decision = route_task(task)

        return decision

    # --------------------------------------------------------
    # PLANNER
    # --------------------------------------------------------

    def run_planner(
        self,
        state: OrchestrationState,
    ) -> AgentResult:

        result = self.executor.run(
            "planner",
            state.task,
            state,
        )

        if result.success:
            state.plan = result.output

        state.record(result)

        return result

    # --------------------------------------------------------
    # CODER
    # --------------------------------------------------------

    def run_coder(
        self,
        state: OrchestrationState,
    ) -> AgentResult:

        task = state.task

        if state.plan:
            task = (
                f"{state.task}\n\n"
                "===== PLANNER OUTPUT =====\n"
                f"{state.plan}"
            )

        if state.debug_result:
            task = (
                f"{task}\n\n"
                "===== DEBUGGER OUTPUT =====\n"
                f"{state.debug_result.output}"
            )

        result = self.executor.run(
            "coder",
            task,
            state,
        )

        state.code_result = result
        state.record(result)

        return result

    # --------------------------------------------------------
    # REVIEWER
    # --------------------------------------------------------

    def run_reviewer(
        self,
        state: OrchestrationState,
    ) -> AgentResult:

        code = ""

        if state.code_result:
            code = state.code_result.output

        task = (
            f"{state.task}\n\n"
            "===== PLAN =====\n"
            f"{state.plan}\n\n"
            "===== CODE RESULT =====\n"
            f"{code}"
        )

        result = self.executor.run(
            "reviewer",
            task,
            state,
        )

        state.review_result = result
        state.record(result)

        return result

    # --------------------------------------------------------
    # TESTER
    # --------------------------------------------------------

    def run_tester(
        self,
        state: OrchestrationState,
    ) -> AgentResult:

        code = ""

        if state.code_result:
            code = state.code_result.output

        review = ""

        if state.review_result:
            review = state.review_result.output

        task = (
            f"{state.task}\n\n"
            "===== CODE =====\n"
            f"{code}\n\n"
            "===== REVIEW =====\n"
            f"{review}"
        )

        result = self.executor.run(
            "tester",
            task,
            state,
        )

        state.test_result = result
        state.record(result)

        return result

    # --------------------------------------------------------
    # DEBUGGER
    # --------------------------------------------------------

    def run_debugger(
        self,
        state: OrchestrationState,
    ) -> AgentResult:

        review = ""

        if state.review_result:
            review = state.review_result.output

        test = ""

        if state.test_result:
            test = state.test_result.output

        task = (
            f"{state.task}\n\n"
            "===== REVIEW FAILURE =====\n"
            f"{review}\n\n"
            "===== TEST FAILURE =====\n"
            f"{test}"
        )

        result = self.executor.run(
            RECOVERY_ROLE,
            task,
            state,
        )

        state.debug_result = result
        state.record(result)

        return result

    # --------------------------------------------------------
    # REVIEW STATUS
    # --------------------------------------------------------

    @staticmethod
    def review_passed(
        result: AgentResult,
    ) -> bool:

        if not result.success:
            return False

        text = result.output.lower()

        failure_markers = (
            "fail",
            "failed",
            "bug",
            "error",
            "critical",
            "reject",
            "unsafe",
        )

        return not any(
            marker in text
            for marker in failure_markers
        )

    # --------------------------------------------------------
    # TEST STATUS
    # --------------------------------------------------------

    @staticmethod
    def test_passed(
        result: AgentResult,
    ) -> bool:

        if not result.success:
            return False

        text = result.output.lower()

        failure_markers = (
            "fail",
            "failed",
            "error",
            "panic",
            "test failed",
        )

        return not any(
            marker in text
            for marker in failure_markers
        )

    # --------------------------------------------------------
    # PIPELINE
    # --------------------------------------------------------

    def execute(
        self,
        task: str,
    ) -> OrchestrationState:

        state = OrchestrationState(
            task=task
        )

        print_header(
            "SYNORA MULTI-AGENT ORCHESTRATOR V3"
        )

        print(
            f"Task: {task}"
        )

        # ----------------------------------------------------
        # ROUTE
        # ----------------------------------------------------

        state.route = self.route(task)

        print()
        print("===== ROUTING =====")
        print(
            f"Role: {state.route.role}"
        )
        print(
            f"Confidence: "
            f"{state.route.confidence:.3f}"
        )

        # ----------------------------------------------------
        # PIPELINE
        # ----------------------------------------------------

        print()
        print("===== PIPELINE =====")

        for index, role in enumerate(
            ROLE_ORDER,
            start=1,
        ):
            print(
                f"{index}. {role}"
            )

        # ----------------------------------------------------
        # PLANNER
        # ----------------------------------------------------

        print()
        print("[1/4] PLANNER")

        planner = self.run_planner(
            state
        )

        if not planner.success:
            print(
                "✗ Planner gagal."
            )
            return self.finalize(
                state,
                "failed",
            )

        print(
            "✓ Planner selesai."
        )

        # ----------------------------------------------------
        # REPAIR LOOP
        # ----------------------------------------------------

        while True:

            # -----------------------------------------------
            # CODER
            # -----------------------------------------------

            print()
            print(
                f"[2/4] CODER "
                f"(round {state.repair_round + 1})"
            )

            coder = self.run_coder(
                state
            )

            if not coder.success:
                print(
                    "✗ Coder gagal."
                )

                if not self.try_repair(
                    state
                ):
                    return self.finalize(
                        state,
                        "failed",
                    )

                continue

            print(
                "✓ Coder selesai."
            )

            # -----------------------------------------------
            # REVIEWER
            # -----------------------------------------------

            print()
            print("[3/4] REVIEWER")

            reviewer = self.run_reviewer(
                state
            )

            if not self.review_passed(
                reviewer
            ):
                print(
                    "✗ Reviewer menemukan masalah."
                )

                if not self.try_repair(
                    state
                ):
                    return self.finalize(
                        state,
                        "review_failed",
                    )

                continue

            print(
                "✓ Reviewer menerima perubahan."
            )

            # -----------------------------------------------
            # TESTER
            # -----------------------------------------------

            print()
            print("[4/4] TESTER")

            tester = self.run_tester(
                state
            )

            if not self.test_passed(
                tester
            ):
                print(
                    "✗ Tester menemukan masalah."
                )

                if not self.try_repair(
                    state
                ):
                    return self.finalize(
                        state,
                        "test_failed",
                    )

                continue

            print(
                "✓ Tester menerima perubahan."
            )

            # -----------------------------------------------
            # SUCCESS
            # -----------------------------------------------

            return self.finalize(
                state,
                "success",
            )

    # --------------------------------------------------------
    # REPAIR
    # --------------------------------------------------------

    def try_repair(
        self,
        state: OrchestrationState,
    ) -> bool:

        if (
            state.repair_round
            >= MAX_REPAIR_ROUNDS
        ):
            print()
            print(
                "✗ Maximum repair round tercapai."
            )

            return False

        state.repair_round += 1

        print()
        print(
            "=" * 60
        )
        print(
            f"RECOVERY ROUND "
            f"{state.repair_round}/"
            f"{MAX_REPAIR_ROUNDS}"
        )
        print(
            "=" * 60
        )

        debugger = self.run_debugger(
            state
        )

        if not debugger.success:
            print(
                "✗ Debugger gagal."
            )
            return False

        print(
            "✓ Debugger selesai."
        )

        return True

    # --------------------------------------------------------
    # FINALIZE
    # --------------------------------------------------------

    def finalize(
        self,
        state: OrchestrationState,
        status: str,
    ) -> OrchestrationState:

        state.metadata["status"] = status

        state.metadata["finished_at"] = now()

        state.metadata["repair_rounds"] = (
            state.repair_round
        )

        print()
        print("=" * 60)
        print("ORCHESTRATION RESULT")
        print("=" * 60)

        print(
            f"Status: {status}"
        )

        print(
            f"Repair rounds: "
            f"{state.repair_round}"
        )

        print(
            f"Agent executions: "
            f"{len(state.history)}"
        )

        print("=" * 60)

        return state

    # --------------------------------------------------------
    # SERIALIZATION
    # --------------------------------------------------------

    @staticmethod
    def serialize(
        state: OrchestrationState,
    ) -> Dict[str, Any]:

        route = None

        if state.route:
            route = {
                "role": state.route.role,
                "confidence": state.route.confidence,
                "reason": state.route.reason,
            }

        return {
            "task": state.task,
            "route": route,
            "plan": state.plan,
            "repair_round": state.repair_round,
            "history": state.history,
            "metadata": state.metadata,
        }

    def save_state(
        self,
        state: OrchestrationState,
        filename: str = "orchestration.json",
    ) -> Path:

        ensure_agent_dir()

        path = AGENT_DIR / filename

        path.write_text(
            json.dumps(
                self.serialize(state),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return path


# ============================================================
# SELF TEST EXECUTOR
# ============================================================

class TestExecutor(AgentExecutor):
    """
    Executor deterministic untuk self-test.

    Tidak memanggil Gemini.
    """

    def __init__(self):
        super().__init__()

        self.register(
            "planner",
            self.planner,
        )

        self.register(
            "coder",
            self.coder,
        )

        self.register(
            "reviewer",
            self.reviewer,
        )

        self.register(
            "tester",
            self.tester,
        )

        self.register(
            "debugger",
            self.debugger,
        )

        self.repair_triggered = False

    @staticmethod
    def planner(
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        return AgentResult(
            role="planner",
            status="success",
            confidence=0.99,
            output=(
                "PLAN OK\n"
                "1. Analisis source.\n"
                "2. Implementasi perubahan.\n"
                "3. Review.\n"
                "4. Test."
            ),
        )

    def coder(
        self,
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        return AgentResult(
            role="coder",
            status="success",
            confidence=0.98,
            output=(
                "CODE OK\n"
                "Perubahan berhasil dibuat."
            ),
        )

    def reviewer(
        self,
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        return AgentResult(
            role="reviewer",
            status="success",
            confidence=0.97,
            output=(
                "REVIEW OK\n"
                "Tidak ditemukan masalah."
            ),
        )

    def tester(
        self,
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        return AgentResult(
            role="tester",
            status="success",
            confidence=0.96,
            output=(
                "TEST OK\n"
                "Semua test berhasil."
            ),
        )

    def debugger(
        self,
        task: str,
        state: OrchestrationState,
    ) -> AgentResult:

        return AgentResult(
            role="debugger",
            status="success",
            confidence=0.95,
            output=(
                "DEBUG OK\n"
                "Masalah berhasil dianalisis."
            ),
        )


# ============================================================
# SELF TEST
# ============================================================

def run_self_test() -> None:

    print_header(
        "SYNORA ORCHESTRATOR V3 SELF TEST"
    )

    executor = TestExecutor()

    orchestrator = SynoraOrchestrator(
        executor=executor
    )

    state = orchestrator.execute(
        "buat endpoint RPC baru"
    )

    if state.route is None:
        raise AssertionError(
            "Route tidak dibuat."
        )

    if state.route.role != "coder":
        raise AssertionError(
            f"Expected coder, got "
            f"{state.route.role}"
        )

    if state.metadata.get("status") != "success":
        raise AssertionError(
            "Pipeline normal gagal."
        )

    roles = [
        item["role"]
        for item in state.history
    ]

    expected_roles = [
        "planner",
        "coder",
        "reviewer",
        "tester",
    ]

    if roles != expected_roles:
        raise AssertionError(
            f"Pipeline salah: "
            f"{roles}"
        )

    print()
    print(
        "PASS: normal pipeline."
    )

    if state.repair_round != 0:
        raise AssertionError(
            "Normal pipeline tidak boleh repair."
        )

    saved = orchestrator.save_state(
        state,
        "orchestrator-v3-test.json",
    )

    if not saved.exists():
        raise AssertionError(
            "State file tidak dibuat."
        )

    saved.unlink()

    print(
        "PASS: state serialization."
    )

    print()
    print("=" * 60)
    print(
        "✓ ORCHESTRATOR V3 SELF TEST PASSED"
    )
    print("=" * 60)


# ============================================================
# CLI
# ============================================================

def main() -> None:

    if len(sys.argv) > 1:

        task = " ".join(
            sys.argv[1:]
        )

        orchestrator = (
            SynoraOrchestrator()
        )

        state = orchestrator.execute(
            task
        )

        orchestrator.save_state(
            state
        )

        return

    run_self_test()


if __name__ == "__main__":
    main()
