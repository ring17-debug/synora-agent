from agent.intelligence.execution_engine_v2 import (
    ExecutionEngineV2,
    ExecutionState,
    STATUS_FAILED,
    STATUS_SUCCESS,
)
from agent.intelligence.verification_engine import (
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    VerificationEngine,
)


def test_verification_result_can_be_attached_to_execution_state():
    state = ExecutionState(
        task="Implement RPC",
        plan="Build RPC endpoint",
        changes=[
            {
                "file": "rpc.py",
                "action": "modify",
            }
        ],
        verification={
            "required": True,
        },
    )

    verifier = VerificationEngine()

    verifier.register(
        "plan_exists",
        lambda current_state: bool(
            current_state.plan
        ),
    )

    result = verifier.verify(state)

    assert result.status == VERIFICATION_PASSED
    assert result.passed is True

    state.metadata["verification"] = (
        result.to_dict()
    )

    assert (
        state.metadata["verification"]["status"]
        == VERIFICATION_PASSED
    )


def test_failed_verification_contains_repair_information():
    state = ExecutionState(
        task="Implement RPC",
        plan="Broken plan",
        verification={
            "required": True,
        },
    )

    verifier = VerificationEngine()

    verifier.register(
        "tests",
        lambda current_state: {
            "passed": False,
            "error": "RPC test failed",
            "evidence": {
                "failed": 1,
            },
        },
    )

    result = verifier.verify(state)

    assert result.status == VERIFICATION_FAILED
    assert result.passed is False
    assert result.errors == (
        "RPC test failed",
    )

    assert result.evidence == (
        {
            "failed": 1,
        },
    )


def test_verification_does_not_modify_execution_state():
    state = ExecutionState(
        task="Immutable verification",
        plan="Existing plan",
        changes=[
            {
                "file": "example.py",
                "action": "modify",
            }
        ],
        verification={
            "required": True,
        },
    )

    original_plan = state.plan
    original_changes = list(
        state.changes
    )

    verifier = VerificationEngine()

    verifier.register(
        "always_pass",
        lambda current_state: True,
    )

    result = verifier.verify(state)

    assert result.passed is True
    assert state.plan == original_plan
    assert state.changes == original_changes


def test_repair_round_can_be_incremented_after_failure():
    engine = ExecutionEngineV2(
        max_repair_rounds=2,
    )

    state = ExecutionState(
        task="Repair RPC",
        status=STATUS_FAILED,
        repair_round=0,
    )

    assert state.repair_round == 0

    state.repair_round += 1

    assert state.repair_round == 1
    assert (
        state.repair_round
        <= engine.max_repair_rounds
    )


def test_repair_limit_is_explicit():
    engine = ExecutionEngineV2(
        max_repair_rounds=2,
    )

    state = ExecutionState(
        task="Repair RPC",
        status=STATUS_FAILED,
        repair_round=2,
    )

    can_repair = (
        state.repair_round
        < engine.max_repair_rounds
    )

    assert can_repair is False


def test_success_state_is_distinct_from_verification_failure():
    success_state = ExecutionState(
        task="Successful task",
        status=STATUS_SUCCESS,
    )

    failed_state = ExecutionState(
        task="Failed task",
        status=STATUS_FAILED,
    )

    assert success_state.status == STATUS_SUCCESS
    assert failed_state.status == STATUS_FAILED
    assert (
        success_state.status
        != failed_state.status
    )
