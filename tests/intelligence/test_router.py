from agent.intelligence.router import (
    IntelligenceRouter,
)


def test_router_debugger():
    router = IntelligenceRouter()

    result = router.route(
        "Ada traceback dan error pada aplikasi"
    )

    assert result.role == "debugger"
    assert result.confidence > 0.5


def test_router_tester():
    router = IntelligenceRouter()

    result = router.route(
        "buat testing dan coverage untuk endpoint"
    )

    assert result.role == "tester"


def test_router_reviewer():
    router = IntelligenceRouter()

    result = router.route(
        "review dan audit security kode ini"
    )

    assert result.role == "reviewer"


def test_router_planner():
    router = IntelligenceRouter()

    result = router.route(
        "buat blueprint arsitektur sistem baru"
    )

    assert result.role == "planner"


def test_router_coder():
    router = IntelligenceRouter()

    result = router.route(
        "implementasikan endpoint RPC baru"
    )

    assert result.role == "coder"


def test_router_unknown_defaults_to_planner():
    router = IntelligenceRouter()

    result = router.route(
        "jelaskan sesuatu yang tidak memiliki intent"
    )

    assert result.role == "planner"
    assert result.confidence == 0.50
