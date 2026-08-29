"""
Tests for Synora AgentOrchestrator.

Coverage:
- classification
- pipeline construction
- pipeline description
- role registration
- verification registration
- normal execution
- verified execution
- repair execution
- routed execution
- dependency injection
"""

from __future__ import annotations

import pytest

from agent.intelligence.execution_engine_v2 import (
    ExecutionEngineV2,
    STATUS_FAILED,
    STATUS_SUCCESS,
)
from agent.intelligence.orchestrator import (
    AgentOrchestrator,
    AgentTask,
)
from agent.intelligence.router import (
    IntelligenceRouter,
)
from agent.intelligence.verification_engine import (
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    VerificationEngine,
)
from agent.intelligence.verified_execution_engine import (
    VerifiedExecutionEngine,
)


# ============================================================
# HELPERS
# ============================================================


def planner_handler(context):
    return {
        "output": "Planner completed",
        "structured": {
            "plan": "Build transaction flow",
        },
    }


def coder_handler(context):
    return {
        "output": "Coder completed",
        "structured": {
            "code": "transaction implementation",
        },
    }


def reviewer_handler(context):
    return {
        "output": "Reviewer completed",
        "structured": {
            "review": "Looks good",
        },
    }


def _tester_handler(context):
    return {
        "output": "Tester completed",
        "structured": {
            "tests": "All tests passed",
        },
    }


# ============================================================
# AGENT TASK
# ============================================================


def test_agent_task_defaults_are_correct():
    task = AgentTask(
        task="Build transaction flow",
        role="planner",
    )

    assert task.task == "Build transaction flow"
    assert task.role == "planner"
    assert task.status == "pending"
    assert task.result == ""
    assert task.metadata == {}


# ============================================================
# CONSTRUCTION
# ============================================================


def test_orchestrator_can_be_created():
    orchestrator = AgentOrchestrator()

    assert isinstance(
        orchestrator.router,
        IntelligenceRouter,
    )

    assert isinstance(
        orchestrator.execution_engine,
        ExecutionEngineV2,
    )

    assert isinstance(
        orchestrator.verification_engine,
        VerificationEngine,
    )

    assert isinstance(
        orchestrator.verified_execution_engine,
        VerifiedExecutionEngine,
    )


def test_orchestrator_accepts_injected_dependencies():
    router = IntelligenceRouter()
    execution = ExecutionEngineV2()
    verification = VerificationEngine()

    verified = VerifiedExecutionEngine(
        execution_engine=execution,
        verification_engine=verification,
    )

    orchestrator = AgentOrchestrator(
        router=router,
        execution_engine=execution,
        verification_engine=verification,
        verified_execution_engine=verified,
    )

    assert orchestrator.router is router
    assert orchestrator.execution_engine is execution
    assert orchestrator.verification_engine is verification
    assert orchestrator.verified_execution_engine is verified


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "router": object(),
        },
        {
            "execution_engine": object(),
        },
        {
            "verification_engine": object(),
        },
        {
            "verified_execution_engine": object(),
        },
    ],
)
def test_orchestrator_rejects_invalid_dependencies(kwargs):
    with pytest.raises(TypeError):
        AgentOrchestrator(**kwargs)


# ============================================================
# CLASSIFY
# ============================================================


def test_classify_returns_routing_decision():
    orchestrator = AgentOrchestrator()

    decision = orchestrator.classify(
        "Fix a bug in the transaction executor"
    )

    assert decision.role
    assert isinstance(
        decision.role,
        str,
    )


@pytest.mark.parametrize(
    "task",
    [
        None,
        123,
        [],
    ],
)
def test_classify_rejects_non_string_task(task):
    orchestrator = AgentOrchestrator()

    with pytest.raises(TypeError):
        orchestrator.classify(task)


# ============================================================
# PIPELINE
# ============================================================


def test_default_pipeline_contains_four_roles():
    orchestrator = AgentOrchestrator()

    pipeline = orchestrator.build_pipeline(
        "Build transaction flow",
        primary_role="planner",
    )

    assert [
        item.role
        for item in pipeline
    ] == [
        "planner",
        "coder",
        "reviewer",
        "tester",
    ]


def test_debugger_pipeline_starts_with_debugger():
    orchestrator = AgentOrchestrator()

    pipeline = orchestrator.build_pipeline(
        "Debug transaction executor",
        primary_role="debugger",
    )

    assert [
        item.role
        for item in pipeline
    ] == [
        "debugger",
        "coder",
        "reviewer",
        "tester",
    ]


def test_tester_pipeline_contains_debugger_and_second_tester():
    orchestrator = AgentOrchestrator()

    pipeline = orchestrator.build_pipeline(
        "Test transaction flow",
        primary_role="tester",
    )

    assert [
        item.role
        for item in pipeline
    ] == [
        "tester",
        "debugger",
        "coder",
        "tester",
    ]


def test_reviewer_pipeline_contains_reviewer_coder_tester():
    orchestrator = AgentOrchestrator()

    pipeline = orchestrator.build_pipeline(
        "Review transaction implementation",
        primary_role="reviewer",
    )

    assert [
        item.role
        for item in pipeline
    ] == [
        "reviewer",
        "coder",
        "tester",
    ]


def test_pipeline_roles_returns_role_names():
    orchestrator = AgentOrchestrator()

    roles = orchestrator.pipeline_roles(
        "Build transaction flow",
        primary_role="planner",
    )

    assert roles == [
        "planner",
        "coder",
        "reviewer",
        "tester",
    ]


@pytest.mark.parametrize(
    "task",
    [
        "",
        "   ",
    ],
)
def test_build_pipeline_rejects_empty_task(task):
    orchestrator = AgentOrchestrator()

    with pytest.raises(ValueError):
        orchestrator.build_pipeline(task)


def test_build_pipeline_rejects_non_string_task():
    orchestrator = AgentOrchestrator()

    with pytest.raises(TypeError):
        orchestrator.build_pipeline(123)


def test_build_pipeline_rejects_empty_primary_role():
    orchestrator = AgentOrchestrator()

    with pytest.raises(ValueError):
        orchestrator.build_pipeline(
            "Build transaction flow",
            primary_role="   ",
        )


def test_build_pipeline_creates_agent_tasks():
    orchestrator = AgentOrchestrator()

    pipeline = orchestrator.build_pipeline(
        "Build transaction flow",
        primary_role="planner",
    )

    assert all(
        isinstance(item, AgentTask)
        for item in pipeline
    )

    assert all(
        item.task == "Build transaction flow"
        for item in pipeline
    )


# ============================================================
# PIPELINE DESCRIPTION
# ============================================================


def test_describe_pipeline_contains_expected_metadata():
    orchestrator = AgentOrchestrator()

    description = orchestrator.describe_pipeline(
        "Build transaction flow"
    )

    assert len(description) == 4

    assert description[0]["step"] == 1
    assert description[0]["role"] == "planner"
    assert description[0]["status"] == "pending"

    for item in description:
        assert "step" in item
        assert "role" in item
        assert "description" in item
        assert "status" in item


# ============================================================
# ROLE REGISTRATION
# ============================================================


def test_register_role_and_has_role():
    orchestrator = AgentOrchestrator()

    assert (
        orchestrator.has_role("planner")
        is False
    )

    orchestrator.register_role(
        "planner",
        planner_handler,
    )

    assert (
        orchestrator.has_role("planner")
        is True
    )


def test_unregister_role():
    orchestrator = AgentOrchestrator()

    orchestrator.register_role(
        "planner",
        planner_handler,
    )

    assert orchestrator.unregister_role(
        "planner"
    ) is True

    assert (
        orchestrator.has_role("planner")
        is False
    )


def test_unregister_unknown_role_returns_false():
    orchestrator = AgentOrchestrator()

    assert orchestrator.unregister_role(
        "unknown"
    ) is False


# ============================================================
# VERIFICATION REGISTRATION
# ============================================================


def test_register_verification_and_has_verification():
    orchestrator = AgentOrchestrator()

    def check(state):
        return {
            "passed": True,
            "name": "state_valid",
            "evidence": {
                "status": state.status,
            },
        }

    assert (
        orchestrator.has_verification(
            "state_valid"
        )
        is False
    )

    orchestrator.register_verification(
        "state_valid",
        check,
    )

    assert (
        orchestrator.has_verification(
            "state_valid"
        )
        is True
    )


def test_unregister_verification():
    orchestrator = AgentOrchestrator()

    orchestrator.register_verification(
        "state_valid",
        lambda state: {
            "passed": True,
            "name": "state_valid",
        },
    )

    assert orchestrator.unregister_verification(
        "state_valid"
    ) is True

    assert (
        orchestrator.has_verification(
            "state_valid"
        )
        is False
    )


def test_unregister_unknown_verification_returns_false():
    orchestrator = AgentOrchestrator()

    assert orchestrator.unregister_verification(
        "unknown"
    ) is False


# ============================================================
# EXECUTION SETUP
# ============================================================


def create_execution_orchestrator():
    execution = ExecutionEngineV2()

    orchestrator = AgentOrchestrator(
        execution_engine=execution,
    )

    orchestrator.register_role(
        "planner",
        planner_handler,
    )

    orchestrator.register_role(
        "coder",
        coder_handler,
    )

    orchestrator.register_role(
        "reviewer",
        reviewer_handler,
    )

    orchestrator.register_role(
        "tester",
        _tester_handler,
    )

    return orchestrator


# ============================================================
# EXECUTE
# ============================================================


def test_execute_runs_pipeline():
    orchestrator = create_execution_orchestrator()

    state = orchestrator.execute(
        "Build transaction flow",
        primary_role="planner",
        verify=False,
    )

    assert state.status == STATUS_SUCCESS

    assert state.plan == "Build transaction flow"


def test_execute_can_use_explicit_pipeline():
    orchestrator = create_execution_orchestrator()

    state = orchestrator.execute(
        "Build transaction flow",
        pipeline=["planner"],
        verify=False,
    )

    assert state.status == STATUS_SUCCESS
    assert state.plan == "Build transaction flow"


def test_execute_passes_context_to_execution():
    orchestrator = create_execution_orchestrator()

    state = orchestrator.execute(
        "Build transaction flow",
        pipeline=["planner"],
        context="Important execution context",
        verify=False,
    )

    assert state.status == STATUS_SUCCESS


# ============================================================
# VERIFIED EXECUTION
# ============================================================


def test_execute_verified_runs_verification():
    orchestrator = create_execution_orchestrator()

    orchestrator.register_verification(
        "state_valid",
        lambda state: {
            "passed": True,
            "name": "state_valid",
            "evidence": {
                "status": state.status,
            },
        },
    )

    state = orchestrator.execute_verified(
        "Build transaction flow",
        pipeline=["planner"],
    )

    assert state.status == STATUS_SUCCESS

    assert (
        state.verification["status"]
        == VERIFICATION_PASSED
    )


def test_execute_verified_can_fail_verification():
    orchestrator = create_execution_orchestrator()

    orchestrator.register_verification(
        "state_invalid",
        lambda state: {
            "passed": False,
            "name": "state_invalid",
            "error": "State tidak valid.",
        },
    )

    state = orchestrator.execute_verified(
        "Build transaction flow",
        pipeline=["planner"],
    )

    assert state.status == STATUS_FAILED

    assert (
        state.verification["status"]
        == VERIFICATION_FAILED
    )


# ============================================================
# REPAIR
# ============================================================


def test_execute_with_repair_can_recover_failed_verification():
    orchestrator = create_execution_orchestrator()

    orchestrator.register_role(
        "repairer",
        lambda context: {
            "output": "Repair completed",
            "structured": {
                "plan": "Fixed plan",
            },
        },
    )

    orchestrator.register_verification(
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

    state = orchestrator.execute_with_repair(
        "Build transaction flow",
        pipeline=["planner"],
    )

    assert state.status == STATUS_SUCCESS
    assert state.plan == "Fixed plan"
    assert state.repair_round == 1

    assert (
        state.verification["status"]
        == VERIFICATION_PASSED
    )


def test_execute_with_repair_respects_zero_rounds():
    orchestrator = create_execution_orchestrator()

    repair_called = False

    def repairer(context):
        nonlocal repair_called
        repair_called = True

        return {
            "output": "Should not execute",
        }

    orchestrator.register_role(
        "repairer",
        repairer,
    )

    orchestrator.register_verification(
        "always_fail",
        lambda state: {
            "passed": False,
            "name": "always_fail",
            "error": "Verification gagal.",
        },
    )

    state = orchestrator.execute_with_repair(
        "Build transaction flow",
        pipeline=["planner"],
        max_repair_rounds=0,
    )

    assert state.status == STATUS_FAILED
    assert state.repair_round == 0
    assert repair_called is False


# ============================================================
# ROUTED EXECUTION
# ============================================================


def test_route_and_execute_uses_router():
    orchestrator = create_execution_orchestrator()

    state = orchestrator.route_and_execute(
        "Build transaction flow",
        verify=False,
    )

    assert state.status == STATUS_SUCCESS


def test_route_and_execute_verified_uses_router():
    orchestrator = create_execution_orchestrator()

    orchestrator.register_verification(
        "state_valid",
        lambda state: {
            "passed": True,
            "name": "state_valid",
        },
    )

    state = orchestrator.route_and_execute_verified(
        "Build transaction flow",
    )

    assert state.status == STATUS_SUCCESS

    assert (
        state.verification["status"]
        == VERIFICATION_PASSED
    )


def test_route_and_execute_with_repair_uses_router():
    orchestrator = create_execution_orchestrator()

    orchestrator.register_role(
        "repairer",
        lambda context: {
            "output": "Repair completed",
            "structured": {
                "plan": "Fixed plan",
            },
        },
    )

    orchestrator.register_verification(
        "plan_ready",
        lambda state: {
            "passed": state.plan == "Fixed plan",
            "name": "plan_ready",
            "error": (
                "Plan invalid."
                if state.plan != "Fixed plan"
                else ""
            ),
            "evidence": {
                "plan": state.plan,
            },
        },
    )

    state = orchestrator.route_and_execute_with_repair(
        "Build transaction flow",
    )

    assert state.status == STATUS_SUCCESS
    assert state.plan == "Fixed plan"
    assert state.repair_round == 1

    assert (
        state.verification["status"]
        == VERIFICATION_PASSED
    )


# ============================================================
# EXPLICIT PIPELINE OVERRIDES ROUTER
# ============================================================


def test_explicit_pipeline_overrides_router():
    orchestrator = create_execution_orchestrator()

    state = orchestrator.execute(
        "Build transaction flow",
        pipeline=["planner"],
        primary_role="tester",
        verify=False,
    )

    assert state.status == STATUS_SUCCESS

    assert state.plan == "Build transaction flow"


# ============================================================
# VERIFICATION ENGINE SHARING
# ============================================================


def test_injected_verification_engine_is_shared():
    verification = VerificationEngine()

    orchestrator = AgentOrchestrator(
        verification_engine=verification,
    )

    assert (
        orchestrator.verification_engine
        is verification
    )

    assert (
        orchestrator.verified_execution_engine
        .verification_engine
        is verification
    )


# ============================================================
# EXECUTION ENGINE SHARING
# ============================================================


def test_injected_execution_engine_is_shared():
    execution = ExecutionEngineV2()

    orchestrator = AgentOrchestrator(
        execution_engine=execution,
    )

    assert (
        orchestrator.execution_engine
        is execution
    )

    assert (
        orchestrator.verified_execution_engine
        .execution_engine
        is execution
    )
