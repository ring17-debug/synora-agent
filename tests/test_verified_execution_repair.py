from __future__ import annotations

from agent.intelligence.execution_engine_v2 import (
    ExecutionEngineV2,
)
from agent.intelligence.verification_engine import (
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    VerificationEngine,
)
from agent.intelligence.verified_execution_engine import (
    VerifiedExecutionEngine,
)


def test_verification_failure_is_repaired_and_reverified():
    verification = VerificationEngine()

    verification.register(
        "plan_ready",
        lambda state: {
            "passed": state.plan == "Fixed plan",
            "name": "plan_ready",
            "error": (
                "Plan belum diperbaiki."
                if state.plan != "Fixed plan"
                else ""
            ),
            "evidence": {
                "plan": state.plan,
            },
        },
    )

    execution = ExecutionEngineV2(
        max_repair_rounds=2,
    )

    execution.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Broken plan",
            },
        },
    )

    execution.register(
        "repairer",
        lambda context: {
            "output": "Repairer fixed plan",
            "structured": {
                "plan": "Fixed plan",
            },
        },
    )

    engine = VerifiedExecutionEngine(
        execution_engine=execution,
        verification_engine=verification,
    )

    state = engine.execute_with_repair(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "success"

    assert state.plan == "Fixed plan"

    assert state.verification[
        "status"
    ] == VERIFICATION_PASSED

    assert state.repair_round == 1

    events = [
        item["event"]
        for item in state.history
    ]

    assert "repair_started" in events
    assert "repair_completed" in events
    assert "verification_retried" in events
    assert "repair_loop_completed" in events


def test_repair_loop_stops_at_max_repair_rounds():
    verification = VerificationEngine()

    verification.register(
        "always_fail",
        lambda state: {
            "passed": False,
            "name": "always_fail",
            "error": "Verification masih gagal.",
            "evidence": {
                "status": state.status,
            },
        },
    )

    execution = ExecutionEngineV2(
        max_repair_rounds=2,
    )

    execution.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Broken plan",
            },
        },
    )

    repair_calls = []

    def repairer(context):
        repair_calls.append(
            context.repair_round
        )

        return {
            "output": "Repair attempted",
            "structured": {
                "plan": (
                    f"Repair {context.repair_round}"
                ),
            },
        }

    execution.register(
        "repairer",
        repairer,
    )

    engine = VerifiedExecutionEngine(
        execution_engine=execution,
        verification_engine=verification,
    )

    state = engine.execute_with_repair(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "failed"

    assert state.verification[
        "status"
    ] == VERIFICATION_FAILED

    assert state.repair_round == 2

    assert repair_calls == [1, 2]

    events = [
        item["event"]
        for item in state.history
    ]

    assert "repair_exhausted" in events


def test_repair_can_be_disabled_with_zero_rounds():
    verification = VerificationEngine()

    verification.register(
        "always_fail",
        lambda state: {
            "passed": False,
            "name": "always_fail",
            "error": "Verification gagal.",
        },
    )

    execution = ExecutionEngineV2()

    execution.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Broken plan",
            },
        },
    )

    repair_called = False

    def repairer(context):
        nonlocal repair_called
        repair_called = True

        return {
            "output": "Should not execute",
        }

    execution.register(
        "repairer",
        repairer,
    )

    engine = VerifiedExecutionEngine(
        execution_engine=execution,
        verification_engine=verification,
    )

    state = engine.execute_with_repair(
        "Build transaction flow",
        ["planner"],
        max_repair_rounds=0,
    )

    assert state.status == "failed"
    assert state.repair_round == 0
    assert repair_called is False

    events = [
        item["event"]
        for item in state.history
    ]

    assert "repair_exhausted" in events


def test_missing_repair_role_does_not_create_infinite_loop():
    verification = VerificationEngine()

    verification.register(
        "always_fail",
        lambda state: {
            "passed": False,
            "name": "always_fail",
            "error": "Verification gagal.",
        },
    )

    execution = ExecutionEngineV2(
        max_repair_rounds=5,
    )

    execution.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Broken plan",
            },
        },
    )

    engine = VerifiedExecutionEngine(
        execution_engine=execution,
        verification_engine=verification,
    )

    state = engine.execute_with_repair(
        "Build transaction flow",
        ["planner"],
        repair_role="repairer",
    )

    assert state.status == "failed"
    assert state.repair_round == 0

    exhausted = [
        item
        for item in state.history
        if item["event"] == "repair_exhausted"
    ]

    assert len(exhausted) == 1
    assert (
        exhausted[0]["reason"]
        == "repair_role_not_registered"
    )


def test_repairer_receives_verification_failure_context():
    verification = VerificationEngine()

    verification.register(
        "required_plan",
        lambda state: {
            "passed": False,
            "name": "required_plan",
            "error": "Plan invalid.",
            "evidence": {
                "plan": state.plan,
            },
        },
    )

    execution = ExecutionEngineV2(
        max_repair_rounds=1,
    )

    execution.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Broken plan",
            },
        },
    )

    captured = {}

    def repairer(context):
        captured["context"] = context.context
        captured["verification"] = dict(
            context.verification
        )

        return {
            "output": "Repair completed",
            "structured": {
                "plan": "Fixed plan",
            },
        }

    execution.register(
        "repairer",
        repairer,
    )

    engine = VerifiedExecutionEngine(
        execution_engine=execution,
        verification_engine=verification,
    )

    state = engine.execute_with_repair(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "failed"

    assert (
        "REPAIR REQUIRED"
        in captured["context"]
    )

    assert (
        "Plan invalid."
        in captured["context"]
    )

    assert (
        captured["verification"]["status"]
        == VERIFICATION_FAILED
    )

    assert (
        captured["verification"]["errors"]
        == ["Plan invalid."]
    )
