from src.action_intents import classify_tool_intent
from src.workspace_analyze_intent import (
    is_workspace_analyze_request,
    workspace_analyze_system_note,
)

_USER_PROMPT = (
    "analyze this project and tell me what this website is about "
    "and what tools are used to make it"
)


def test_is_workspace_analyze_request():
    assert is_workspace_analyze_request(_USER_PROMPT)
    assert is_workspace_analyze_request("review the codebase")
    assert is_workspace_analyze_request("what is this website about")
    assert not is_workspace_analyze_request("make a txt file named 1.txt")


def test_workspace_analyze_system_note():
    note = workspace_analyze_system_note(_USER_PROMPT)
    assert note is not None
    assert "Do NOT say you lack filesystem access" in note
    assert "read_file" in note


def test_analyze_promotes_chat_to_agent():
    intent = classify_tool_intent(_USER_PROMPT)
    assert intent.needs_tools
    assert intent.category == "workspace"


def test_collect_manifest_paths(tmp_path):
    from src.workspace_analyze_orchestration import _collect_manifest_paths

    (tmp_path / "README.md").write_text("# Hi", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "main.py").write_text("print(1)", encoding="utf-8")

    paths = _collect_manifest_paths(str(tmp_path))
    assert "README.md" in paths
    assert "package.json" in paths
    assert "src/main.py" in paths
