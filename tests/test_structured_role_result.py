from agent.intelligence.execution_bridge import RoleExecutionBridge
from agent.intelligence.execution_engine_v2 import (
    AgentExecutionContext,
)
from agent.intelligence.role_engine import (
    RoleEngine,
    RoleExecutionResult,
)


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
        return RoleExecutionResult(
            role=role,
            success=True,
            output="planner output",
            metadata={
                "provider": "test",
            },
            structured={
                "plan": "Implement RPC integration",
                "changes": [
                    {
                        "file": "example.py",
                        "action": "modify",
                    }
                ],
                "verification": {
                    "required": True,
                },
            },
        )


def test_role_result_preserves_structured_payload():
    result = RoleExecutionResult(
        role="planner",
        success=True,
        output="planner output",
        metadata={"provider": "test"},
        structured={
            "plan": "Implement RPC integration",
            "changes": [
                {
                    "file": "example.py",
                    "action": "modify",
                }
            ],
            "verification": {
                "required": True,
            },
        },
    )

    data = result.to_dict()

    assert data["structured"]["plan"] == (
        "Implement RPC integration"
    )
    assert data["structured"]["changes"][0]["file"] == (
        "example.py"
    )
    assert data["structured"]["verification"]["required"] is True


def test_execution_bridge_forwards_structured_payload():
    bridge = RoleExecutionBridge(
        FakeRoleEngine()
    )

    context = AgentExecutionContext(
        task="Implement RPC integration",
        role="planner",
        context="Synora project",
        plan="",
        changes=(),
        verification={},
        repair_round=0,
        previous_results=(),
    )

    result = bridge.handler(context)

    assert result["success"] is True
    assert result["output"] == "planner output"
    assert result["structured"]["plan"] == (
        "Implement RPC integration"
    )
    assert result["structured"]["changes"][0]["file"] == (
        "example.py"
    )
    assert result["structured"]["verification"]["required"] is True
