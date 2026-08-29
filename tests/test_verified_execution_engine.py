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


def test_verified_execution_preserves_legacy_behavior_without_checks():
    engine = VerifiedExecutionEngine(
        execution_engine=ExecutionEngineV2()
    )

    engine.execution_engine.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Build transaction flow",
            },
        },
    )

    state = engine.execute(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "success"
    assert state.plan == "Build transaction flow"
    assert state.metadata[
        "verification_performed"
    ] is False
    assert state.metadata[
        "verification_status"
    ] == "skipped"


def test_verified_execution_passes_when_all_checks_pass():
    verification = VerificationEngine()

    verification.register(
        "plan_exists",
        lambda state: {
            "passed": bool(state.plan),
            "name": "plan_exists",
            "evidence": state.plan,
        },
    )

    engine = VerifiedExecutionEngine(
        execution_engine=ExecutionEngineV2(),
        verification_engine=verification,
    )

    engine.execution_engine.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Build transaction flow",
                "verification": {
                    "required": True,
                },
            },
        },
    )

    state = engine.execute(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "success"

    assert state.verification[
        "status"
    ] == VERIFICATION_PASSED

    assert state.verification[
        "passed"
    ] is True

    assert state.verification[
        "checks"
    ][0]["name"] == "plan_exists"

    assert state.metadata[
        "verification_performed"
    ] is True


def test_verified_execution_fails_when_check_fails():
    verification = VerificationEngine()

    verification.register(
        "required_plan",
        lambda state: {
            "passed": bool(state.plan),
            "name": "required_plan",
            "error": "Plan belum tersedia.",
        },
    )

    engine = VerifiedExecutionEngine(
        execution_engine=ExecutionEngineV2(),
        verification_engine=verification,
    )

    engine.execution_engine.register(
        "coder",
        lambda context: {
            "output": "Coder completed",
        },
    )

    state = engine.execute(
        "Implement transaction flow",
        ["coder"],
    )

    assert state.status == "failed"

    assert state.verification[
        "status"
    ] == VERIFICATION_FAILED

    assert state.verification[
        "passed"
    ] is False

    assert (
        "Plan belum tersedia."
        in state.verification["errors"]
    )


def test_execute_verified_requires_registered_checks():
    engine = VerifiedExecutionEngine()

    engine.execution_engine.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Build transaction flow",
            },
        },
    )

    state = engine.execute_verified(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "failed"

    assert state.verification[
        "status"
    ] == "pending"

    assert state.verification[
        "passed"
    ] is False


def test_verify_updates_execution_history():
    verification = VerificationEngine()

    verification.register(
        "state_valid",
        lambda state: {
            "passed": True,
            "name": "state_valid",
            "evidence": {
                "status": state.status,
            },
        },
    )

    engine = VerifiedExecutionEngine(
        verification_engine=verification,
    )

    engine.execution_engine.register(
        "planner",
        lambda context: {
            "output": "Planner completed",
            "structured": {
                "plan": "Build transaction flow",
            },
        },
    )

    state = engine.execute(
        "Build transaction flow",
        ["planner"],
    )

    assert state.status == "success"

    events = [
        item["event"]
        for item in state.history
    ]

    assert (
        "verification_completed"
        in events
    )

    assert state.verification[
        "evidence"
    ][0]["status"] == "running"
