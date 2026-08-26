from __future__ import annotations

from dataclasses import dataclass, field

from .router import IntelligenceRouter, RoutingDecision
from .roles import get_role


@dataclass
class AgentTask:
    task: str
    role: str
    status: str = "pending"
    result: str = ""
    metadata: dict = field(default_factory=dict)


class AgentOrchestrator:
    """
    Mengatur alur kerja agent.

    Pipeline default:

        Planner
          ↓
        Coder
          ↓
        Reviewer
          ↓
        Tester

    Debugging dapat masuk ketika verification gagal.
    """

    def __init__(self):
        self.router = IntelligenceRouter()

    def classify(self, task: str) -> RoutingDecision:
        return self.router.route(task)

    def build_pipeline(
        self,
        task: str,
        primary_role: str | None = None,
    ) -> list[AgentTask]:

        if primary_role is None:
            decision = self.classify(task)
            primary_role = decision.role

        if primary_role == "debugger":
            roles = [
                "debugger",
                "coder",
                "reviewer",
                "tester",
            ]

        elif primary_role == "tester":
            roles = [
                "tester",
                "debugger",
                "coder",
                "tester",
            ]

        elif primary_role == "reviewer":
            roles = [
                "reviewer",
                "coder",
                "tester",
            ]

        else:
            roles = [
                "planner",
                "coder",
                "reviewer",
                "tester",
            ]

        return [
            AgentTask(
                task=task,
                role=role,
            )
            for role in roles
        ]

    def describe_pipeline(
        self,
        task: str,
    ) -> list[dict]:

        pipeline = self.build_pipeline(task)

        result = []

        for index, item in enumerate(
            pipeline,
            start=1,
        ):
            role = get_role(item.role)

            result.append(
                {
                    "step": index,
                    "role": role.name,
                    "description": role.description,
                    "status": item.status,
                }
            )

        return result
