from agent.intelligence.roles import (
    ROLES,
    get_role,
    list_roles,
)


def test_all_roles_exist():
    expected = {
        "planner",
        "coder",
        "reviewer",
        "tester",
        "debugger",
    }

    assert expected.issubset(ROLES.keys())


def test_get_role():
    role = get_role("coder")

    assert role.name == "coder"
    assert role.priority == 20
    assert role.description


def test_get_role_unknown():
    try:
        get_role("unknown")
    except ValueError as exc:
        assert "Unknown agent role" in str(exc)
    else:
        raise AssertionError(
            "get_role() harus menolak role tidak dikenal"
        )


def test_list_roles_priority_order():
    roles = list_roles()

    assert roles == [
        "planner",
        "coder",
        "reviewer",
        "tester",
        "debugger",
    ]


def test_role_instructions_are_not_empty():
    for name, role in ROLES.items():
        assert name == role.name
        assert role.system_instruction.strip()
