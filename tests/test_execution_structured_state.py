from agent.intelligence.execution_bridge import RoleExecutionBridge
from agent.intelligence.execution_engine_v2 import (
    ExecutionEngineV2,
)
from agent.intelligence.role_engine import RoleExecutionResult


class FakeRoleEngine:
    def run(
        self,
        *,
        role,
        task,
        context="",
        previous_result="",
        memory_context="",
    ):
        if role == "planner":
            return RoleExecutionResult(
                role=role,
                success=True,
                output="Plan created",
                metadata={"provider": "test"},
                structured={
                    "plan": "Implement RPC execution",
                    "changes": [
                        {
                            "file": "rpc.py",
                            "action": "modify",
                        },
                        {
                            "file": "tests/rpc.py",
                            "action": "add",
                        },
                    ],
                    "verification": {
                        "required": True,
                        "tests": [
                            "pytest",
                            "cargo test",
                        ],
                    },
                },
            )

        return RoleExecutionResult(
            role=role,
            success=True,
            output=f"{role} completed",
            metadata={"provider": "test"},
        )


def test_structured_role_result_reaches_execution_state():
    engine = ExecutionEngineV2()

    bridge = RoleExecutionBridge(
        FakeRoleEngine()
    )

    bridge.register_roles(
        engine,
        ["planner", "coder"],
    )

    state = engine.execute(
        "Implement RPC execution",
        ["planner"],
        context="Synora test context",
    )

    assert state.status == "success"

    assert state.plan == (
        "Implement RPC execution"
    )

    assert state.changes == [
        {
            "file": "rpc.py",
            "action": "modify",
        },
        {
            "file": "tests/rpc.py",
            "action": "add",
        },
    ]

    assert state.verification == {
        "required": True,
        "tests": [
            "pytest",
            "cargo test",
        ],
    }

    assert len(state.results) == 1
    assert state.results[0].role == "planner"
    assert state.results[0].status == "success"

    assert state.history


def test_structured_state_is_available_to_next_role():
    class ContextAwareRoleEngine:
        def run(
            self,
            *,
            role,
            task,
            context="",
            previous_result="",
            memory_context="",
        ):
            if role == "planner":
                return RoleExecutionResult(
                    role=role,
                    success=True,
                    output="Planner completed",
                    structured={
                        "plan": "Build transaction flow",
                        "changes": [
                            {
                                "file": "transaction.py",
                                "action": "modify",
                            }
                        ],
                        "verification": {
                            "required": True,
                        },
                    },
                )

            assert (
                "CURRENT PLAN:"
                in context
            )

            assert (
                "CURRENT CHANGES:"
                in context
            )

            assert (
                "CURRENT VERIFICATION:"
                in context
            )

            return RoleExecutionResult(
                role=role,
                success=True,
                output="Coder completed",
            )

    engine = ExecutionEngineV2()

    bridge = RoleExecutionBridge(
        ContextAwareRoleEngine()
    )

    bridge.register_roles(
        engine,
        ["planner", "coder"],
    )

    state = engine.execute(
        "Build transaction flow",
        ["planner", "coder"],
    )

    assert state.status == "success"
    assert len(state.results) == 2
    assert state.results[0].role == "planner"
    assert state.results[1].role == "coder"
