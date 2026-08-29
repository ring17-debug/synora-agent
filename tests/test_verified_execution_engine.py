"""
Tests for Synora Verified Execution Engine.
"""

from agent.intelligence.execution_engine_v2 import (
    ExecutionEngineV2,
    STATUS_FAILED,
    STATUS_SUCCESS,
)
from agent.intelligence.verification_engine import (
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    VerificationEngine,
)
from agent.intelligence.verified_execution_engine import (
    VerifiedExecutionEngine,
)


def test_verify_passes_successful_execution():
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

    assert state.status == STATUS_SUCCESS

    assert state.verification["status"] == VERIFICATION_PASSED

    assert state.verification["passed"] is True

    assert state.verification["evidence"][0]["status"] == STATUS_SUCCESS


def test_verify_fails_when_required_check_fails():
    verification = VerificationEngine()

    verification.register(
        "state_valid",
        lambda state: {
            "passed": False,
            "name": "state_valid",
            "error": "State invalid.",
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

    assert state.status == STATUS_FAILED

    assert state.verification["status"] == VERIFICATION_FAILED

    assert state.verification["passed"] is False

    assert state.verification["errors"] == [
        "State invalid.",
    ]


def test_register_and_unregister_verification():
    engine = VerifiedExecutionEngine()

    engine.register_verification(
        "custom_check",
        lambda state: {
            "passed": True,
            "name": "custom_check",
        },
    )

    assert engine.has_verification(
        "custom_check"
    )

    assert engine.unregister_verification(
        "custom_check"
    )

    assert not engine.has_verification(
        "custom_check"
    )


def test_verify_rejects_invalid_state():
    engine = VerifiedExecutionEngine()

    try:
        engine.verify(None)
    except TypeError as exc:
        assert "ExecutionState" in str(exc)
    else:
        raise AssertionError(
            "verify() harus menolak state yang bukan ExecutionState."
        )


def test_verification_metadata_is_propagated():
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

    assert (
        state.metadata["verification_status"]
        == VERIFICATION_PASSED
    )

    assert (
        state.metadata["verification_passed"]
        is True
    )

    assert (
        state.metadata["verification_performed"]
        is True
    )

    events = [
        item["event"]
        for item in state.history
    ]

    assert "verification_completed" in events
