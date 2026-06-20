from src.agent_model_router import model_param_size_b, model_supports_native_tools


def test_model_param_size_b():
    assert model_param_size_b("Qwen2.5-Coder-7B-Instruct") == 7.0
    assert model_param_size_b("Qwen/Qwen3-14B-Instruct") == 14.0
    assert model_param_size_b("gpt-4o") == 0.0


def test_model_supports_native_tools():
    assert model_supports_native_tools("Qwen2.5-Coder-7B-Instruct")
    assert model_supports_native_tools("deepseek-chat")
    assert not model_supports_native_tools("deepseek-r1-distill")
