from __future__ import annotations

from agent.intelligence.execution_engine_v2 import (
    ExecutionEngineV2,
    ExecutionState,
    STATUS_ABORTED,
    STATUS_FAILED,
    STATUS_SUCCESS,
)


def test_pipeline_preserves_role_order() -> None:
    engine = ExecutionEngineV2()

    calls: list[str] = []

    def handler(context):
        calls.append(context.role)

        return {
            "output": f"{context.role} completed",
        }

    for role in (
        "planner",
        "coder",
        "reviewer",
        "tester",
    ):
        engine.register(role, handler)

    state = engine.execute(
        "implement RPC v2",
        [
            "planner",
            "coder",
            "reviewer",
            "tester",
        ],
    )

    assert state.status == STATUS_SUCCESS

    assert calls == [
        "planner",
        "coder",
        "reviewer",
        "tester",
    ]

    assert [
        result.role
        for result in state.results
    ] == calls


def test_plan_is_propagated_to_next_role() -> None:
    engine = ExecutionEngineV2()

    observed_plans: list[str] = []

    def planner(context):
        return {
            "output": "Plan generated.",
            "plan": "Implement endpoint -> add tests -> verify",
        }

    def coder(context):
        observed_plans.append(context.plan)

        return {
            "output": "Code implemented.",
        }

    engine.register("planner", planner)
    engine.register("coder", coder)

    state = engine.execute(
        "implement endpoint",
        [
            "planner",
            "coder",
        ],
    )

    assert state.status == STATUS_SUCCESS

    assert state.plan == (
        "Implement endpoint -> add tests -> verify"
    )

    assert observed_plans == [
        "Implement endpoint -> add tests -> verify"
    ]


def test_changes_are_propagated_to_next_role() -> None:
    engine = ExecutionEngineV2()

    observed_changes = []

    def coder(context):
        return {
            "output": "Changed files.",
            "changes": [
                {
                    "file": "agent/example.py",
                    "action": "modified",
                },
            ],
        }

    def reviewer(context):
        observed_changes.append(
            list(context.changes)
        )

        return {
            "output": "Review completed.",
        }

    engine.register("coder", coder)
    engine.register("reviewer", reviewer)

    state = engine.execute(
        "modify example",
        [
            "coder",
            "reviewer",
        ],
    )

    assert state.status == STATUS_SUCCESS

    assert state.changes == [
        {
            "file": "agent/example.py",
            "action": "modified",
        }
    ]

    assert observed_changes == [
        [
            {
                "file": "agent/example.py",
                "action": "modified",
            }
        ]
    ]


def test_verification_is_propagated_to_next_role() -> None:
    engine = ExecutionEngineV2()

    observed_verification = []

    def tester(context):
        return {
            "output": "Tests executed.",
            "verification": {
                "status": "PASS",
                "executed": True,
            },
        }

    def next_role(context):
        observed_verification.append(
            dict(context.verification)
        )

        return {
            "output": "Verification observed.",
        }

    engine.register("tester", tester)
    engine.register("next", next_role)

    state = engine.execute(
        "run tests",
        [
            "tester",
            "next",
        ],
    )

    assert state.status == STATUS_SUCCESS

    assert state.verification == {
        "status": "PASS",
        "executed": True,
    }

    assert observed_verification == [
        {
            "status": "PASS",
            "executed": True,
        }
    ]


def test_previous_results_are_available_to_next_role() -> None:
    engine = ExecutionEngineV2()

    observed_previous = []

    def planner(context):
        return {
            "output": "Planner result",
        }

    def coder(context):
        observed_previous.append(
            [
                result.output
                for result in context.previous_results
            ]
        )

        return {
            "output": "Coder result",
        }

    engine.register("planner", planner)
    engine.register("coder", coder)

    state = engine.execute(
        "test previous results",
        [
            "planner",
            "coder",
        ],
    )

    assert state.status == STATUS_SUCCESS

    assert observed_previous == [
        [
            "Planner result",
        ]
    ]


def test_agent_reported_failure_stops_pipeline() -> None:
    engine = ExecutionEngineV2()

    calls: list[str] = []

    def planner(context):
        calls.append("planner")

        return {
            "output": "Plan",
        }

    def coder(context):
        calls.append("coder")

        return {
            "output": "Coder failed",
            "success": False,
            "error": "Compilation failed",
        }

    def reviewer(context):
        calls.append("reviewer")

        return {
            "output": "Should not execute",
        }

    engine.register("planner", planner)
    engine.register("coder", coder)
    engine.register("reviewer", reviewer)

    state = engine.execute(
        "test failure",
        [
            "planner",
            "coder",
            "reviewer",
        ],
    )

    assert state.status == STATUS_FAILED

    assert calls == [
        "planner",
        "coder",
    ]

    assert state.results[-1].status == STATUS_FAILED

    assert state.results[-1].error == (
        "Compilation failed"
    )


def test_missing_handler_fails_safely() -> None:
    engine = ExecutionEngineV2()

    state = engine.execute(
        "missing role",
        [
            "planner",
        ],
    )

    assert state.status == STATUS_FAILED

    assert state.results[0].status == STATUS_FAILED

    assert "tidak terdaftar" in (
        state.results[0].error
    ).lower()


def test_repair_cycle_increments_round() -> None:
    engine = ExecutionEngineV2(
        max_repair_rounds=2,
    )

    calls: list[str] = []

    def handler(context):
        calls.append(context.role)

        return {
            "output": f"{context.role} completed",
        }

    for role in (
        "debugger",
        "coder",
        "reviewer",
        "tester",
    ):
        engine.register(role, handler)

    state = ExecutionState(
        task="repair task",
    )

    state.status = STATUS_FAILED

    repaired = engine.execute_repair(state)

    assert repaired is state

    assert state.status == STATUS_SUCCESS

    assert state.repair_round == 1

    assert calls == [
        "debugger",
        "coder",
        "reviewer",
        "tester",
    ]


def test_repair_limit_aborts() -> None:
    engine = ExecutionEngineV2(
        max_repair_rounds=1,
    )

    state = ExecutionState(
        task="repair limit",
        repair_round=1,
    )

    result = engine.execute_repair(state)

    assert result is state

    assert state.status == STATUS_ABORTED

    assert state.repair_round == 1

    assert state.history[-1]["event"] == (
        "repair_aborted"
    )


def test_serialization_contains_results_as_list() -> None:
    engine = ExecutionEngineV2()

    def handler(context):
        return {
            "output": "done",
        }

    engine.register("coder", handler)

    state = engine.execute(
        "serialization test",
        [
            "coder",
        ],
    )

    serialized = engine.serialize(state)

    assert isinstance(
        serialized["results"],
        list,
    )

    assert serialized["results"][0]["role"] == "coder"

    assert serialized["results"][0]["status"] == STATUS_SUCCESS


def test_history_records_execution_events() -> None:
    engine = ExecutionEngineV2()

    def handler(context):
        return {
            "output": "done",
        }

    engine.register("coder", handler)

    state = engine.execute(
        "history test",
        [
            "coder",
        ],
    )

    events = [
        item["event"]
        for item in state.history
    ]

    assert events == [
        "execution_started",
        "agent_started",
        "agent_completed",
        "execution_completed",
    ]
