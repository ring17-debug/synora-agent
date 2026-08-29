from agent.intelligence.role_engine import (
    RoleEngine,
    clean_role_text,
    parse_json_result,
)


def test_clean_role_text():
    value = """```text
hello world
```"""

    assert clean_role_text(value) == "hello world"


def test_parse_json_direct():
    value = '{"status": "success"}'

    result = parse_json_result(value)

    assert result == {
        "status": "success",
    }


def test_parse_json_markdown():
    value = """```json
{"status": "success"}
```"""

    result = parse_json_result(value)

    assert result == {
        "status": "success",
    }


def test_parse_json_embedded():
    value = """
Here is the result:

{"status": "success", "role": "coder"}

Done.
"""

    result = parse_json_result(value)

    assert result == {
        "status": "success",
        "role": "coder",
    }


def test_parse_json_invalid():
    result = parse_json_result(
        "this is not json"
    )

    assert result is None


def test_build_prompt():
    prompt = RoleEngine.build_prompt(
        role="coder",
        task="Implement RPC endpoint",
        context="Existing RPC service",
        previous_result="Planner completed",
    )

    assert "SYNORA ROLE EXECUTION" in prompt
    assert "ROLE: coder" in prompt
    assert "Implement RPC endpoint" in prompt
    assert "Existing RPC service" in prompt
    assert "Planner completed" in prompt
    assert "API key" in prompt


def test_build_prompt_rejects_empty_task():
    try:
        RoleEngine.build_prompt(
            role="coder",
            task="",
        )
    except ValueError as exc:
        assert "task tidak boleh kosong" in str(exc)
    else:
        raise AssertionError(
            "build_prompt() harus menolak task kosong"
        )
