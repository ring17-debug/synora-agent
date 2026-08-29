from agent.intelligence.execution_engine_v2 import (
    ExecutionState,
)
from agent.intelligence.verification_engine import (
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    VERIFICATION_PENDING,
    VERIFICATION_SKIPPED,
    VerificationEngine,
)


def test_required_verification_without_checks_is_pending():
    state = ExecutionState(
        task="Test verification",
        verification={
            "required": True,
        },
    )

    engine = VerificationEngine()

    result = engine.verify(state)

    assert result.status == VERIFICATION_PENDING
    assert result.passed is False
    assert result.required is True
    assert result.metadata[
        "verification_performed"
    ] is False


def test_optional_verification_without_checks_is_skipped():
    state = ExecutionState(
        task="Test optional verification",
        verification={
            "required": False,
        },
    )

    engine = VerificationEngine()

    result = engine.verify(state)

    assert result.status == VERIFICATION_SKIPPED
    assert result.passed is True
    assert result.required is False


def test_all_verification_checks_must_pass():
    state = ExecutionState(
        task="Run checks",
        verification={
            "required": True,
        },
    )

    engine = VerificationEngine()

    engine.register(
        "plan_exists",
        lambda current_state: bool(
            current_state.task
        ),
    )

    engine.register(
        "changes_valid",
        lambda current_state: {
            "passed": True,
            "evidence": {
                "checked": True,
            },
        },
    )

    result = engine.verify(state)

    assert result.status == VERIFICATION_PASSED
    assert result.passed is True
    assert len(result.checks) == 2
    assert result.evidence[0] == {
        "checked": True,
    }


def test_failed_check_produces_failure():
    state = ExecutionState(
        task="Run failing check",
        verification={
            "required": True,
        },
    )

    engine = VerificationEngine()

    engine.register(
        "unit_tests",
        lambda current_state: {
            "passed": False,
            "error": "1 test failed",
        },
    )

    result = engine.verify(state)

    assert result.status == VERIFICATION_FAILED
    assert result.passed is False
    assert result.errors == (
        "1 test failed",
    )


def test_exception_in_check_is_failure():
    state = ExecutionState(
        task="Run exception check",
    )

    engine = VerificationEngine()

    def broken_check(current_state):
        raise RuntimeError(
            "verification crashed"
        )

    engine.register(
        "broken_check",
        broken_check,
    )

    result = engine.verify(state)

    assert result.status == VERIFICATION_FAILED
    assert result.passed is False
    assert result.errors == (
        "verification crashed",
    )


def test_check_can_be_removed():
    engine = VerificationEngine()

    engine.register(
        "example",
        lambda state: True,
    )

    assert engine.has_check(
        "example"
    )

    assert engine.unregister(
        "example"
    )

    assert not engine.has_check(
        "example"
    )
