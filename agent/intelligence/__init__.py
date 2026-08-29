"""
Synora Agent Intelligence public API.
"""

from .providers import (
    GeminiKeyPool,
    ProviderKey,
)

from .gemini_adapter import (
    GeminiAdapter,
    GeminiResponse,
    create_gemini_adapter,
)

from .role_engine import (
    RoleEngine,
    RoleResult,
    clean_role_text,
    parse_json_result,
)

from .roles import (
    AgentRole,
    ROLES,
    get_role,
    list_roles,
)

from .router import (
    IntelligenceRouter,
    RoutingDecision,
)

from .orchestrator import (
    AgentOrchestrator,
    AgentTask,
)

from .decision_engine import (
    AgentDecision,
    DecisionEngine,
    ACTION_PLAN,
    ACTION_CODE,
    ACTION_REVIEW,
    ACTION_TEST,
    ACTION_DEBUG,
    ACTION_REPAIR,
    ACTION_FINISH,
    ACTION_ABORT,
    create_decision_engine,
)

from .execution_engine import (
    ExecutionEngine,
)

from .execution_engine_v2 import (
    AgentExecutionResult,
    ExecutionState,
    ExecutionEngineV2,
    create_execution_engine,
)

from .context_engine import (
    ContextEngine,
)

from .memory_engine import (
    MemoryEngine,
)

from .agent_runtime import (
    AgentRuntime,
    AgentRuntimeResult,
    create_agent_runtime,
)


from .verification_engine import (
    VerificationCheck,
    VerificationResult,
    VerificationEngine,
    VERIFICATION_PENDING,
    VERIFICATION_PASSED,
    VERIFICATION_FAILED,
    VERIFICATION_SKIPPED,
    create_verification_engine,
)


# ============================================================
# GLOBAL ROUTER
# ============================================================

_router = IntelligenceRouter()


# ============================================================
# COMPATIBILITY API
# ============================================================


def route_task(
    task: str,
) -> RoutingDecision:
    return _router.route(task)


def get_role_prompt(
    role_name: str,
) -> str:
    role = get_role(role_name)
    return role.system_instruction


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    # Providers
    "GeminiKeyPool",
    "ProviderKey",

    # Gemini
    "GeminiAdapter",
    "GeminiResponse",
    "create_gemini_adapter",

    # Role engine
    "RoleEngine",
    "RoleResult",
    "clean_role_text",
    "parse_json_result",

    # Roles
    "AgentRole",
    "ROLES",
    "get_role",
    "list_roles",

    # Router
    "IntelligenceRouter",
    "RoutingDecision",
    "route_task",
    "get_role_prompt",

    # Orchestrator
    "AgentOrchestrator",
    "AgentTask",

    # Decision
    "AgentDecision",
    "DecisionEngine",
    "ACTION_PLAN",
    "ACTION_CODE",
    "ACTION_REVIEW",
    "ACTION_TEST",
    "ACTION_DEBUG",
    "ACTION_REPAIR",
    "ACTION_FINISH",
    "ACTION_ABORT",
    "create_decision_engine",

    # Execution
    "ExecutionEngine",
    "AgentExecutionResult",
    "ExecutionState",
    "ExecutionEngineV2",
    "create_execution_engine",

    # Verification
    "VerificationCheck",
    "VerificationResult",
    "VerificationEngine",
    "VERIFICATION_PENDING",
    "VERIFICATION_PASSED",
    "VERIFICATION_FAILED",
    "VERIFICATION_SKIPPED",
    "create_verification_engine",

    # Context
    "ContextEngine",

    # Memory
    "MemoryEngine",

    # Runtime
    "AgentRuntime",
    "AgentRuntimeResult",
    "create_agent_runtime",
]
