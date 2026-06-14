from src.llm_core import (
    _flatten_native_tool_history,
    _needs_local_openai_compat_flatten,
    _prepare_openai_compat_messages,
)


def test_flatten_native_tool_round_to_plain_text():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run bash"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "STDERR: make: *** No rule to make target 'a'.  Stop."},
        {"role": "user", "content": "make a txt file name it 1"},
    ]
    flat = _flatten_native_tool_history(msgs)
    assert [m["role"] for m in flat] == ["system", "user", "assistant", "user", "user"]
    assert all(isinstance(m.get("content"), str) for m in flat)
    assert "Tool execution results" in flat[3]["content"]
    assert "make target" in flat[3]["content"]
    assert flat[-1]["content"] == "make a txt file name it 1"


def test_local_host_docker_internal_needs_flatten():
    assert _needs_local_openai_compat_flatten(
        "http://host.docker.internal:8080/v1/chat/completions", "openai"
    )


def test_cloud_api_does_not_need_flatten():
    assert not _needs_local_openai_compat_flatten(
        "https://api.openai.com/v1/chat/completions", "openai"
    )


def test_prepare_openai_compat_messages_flattens_for_local():
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "x", "type": "function", "function": {"name": "bash", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": "x", "content": "err"},
    ]
    out = _prepare_openai_compat_messages(
        msgs, "http://host.docker.internal:8080/v1/chat/completions", "openai",
    )
    assert not any(m.get("role") == "tool" for m in out)
