from src.action_intents import classify_tool_intent, message_needs_tools
from src.direct_shell import is_direct_shell_command, is_english_make_phrase
from src.workspace_file_intent import (
    is_workspace_file_create_request,
    workspace_file_create_system_note,
)

_USER_PROMPT = (
    "make a txt file name it 1 do the same thing incrementing up to 10 and save them"
)


def test_english_make_phrase_not_direct_shell():
    assert is_english_make_phrase(_USER_PROMPT)
    assert not is_direct_shell_command(_USER_PROMPT)


def test_gnu_make_still_direct_shell():
    assert is_direct_shell_command("make install")
    assert is_direct_shell_command("make build")
    assert is_direct_shell_command("make -j4 all")
    assert not is_english_make_phrase("make install")


def test_workspace_file_create_intent_detected():
    assert is_workspace_file_create_request(_USER_PROMPT)
    note = workspace_file_create_system_note(_USER_PROMPT)
    assert note is not None
    assert "write_file" in note
    assert "GNU `make`" in note or "GNU make" in note


def test_file_create_promotes_chat_to_agent():
    assert message_needs_tools(_USER_PROMPT)
    intent = classify_tool_intent(_USER_PROMPT)
    assert intent.needs_tools
    assert intent.category == "workspace"


def test_create_file_variants():
    for msg in (
        "create a text file called notes.txt",
        "write a file named output.log",
        "please add a txt file in the workspace",
    ):
        assert is_workspace_file_create_request(msg)
        assert message_needs_tools(msg)
