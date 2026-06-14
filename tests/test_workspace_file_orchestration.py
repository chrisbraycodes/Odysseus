from src.workspace_file_intent import is_workspace_file_create_request
from src.workspace_file_orchestration import (
    is_bash_file_creation_attempt,
    parse_numbered_file_batch,
)

_USER_PROMPT = (
    "make a txt file name it 1 do the same thing incrementing up to 10 and save them"
)


def test_parse_numbered_file_batch():
    batch = parse_numbered_file_batch(_USER_PROMPT)
    assert batch is not None
    assert len(batch) == 10
    assert batch[0] == ("1.txt", "1")
    assert batch[9] == ("10.txt", "10")


def test_bash_loop_redirect_blocked():
    cmd = 'for i in {1..10}; do\n    echo "${i}.txt" > "${i}.txt"\ndone'
    assert is_bash_file_creation_attempt(cmd)


def test_inline_write_file_parsing():
    import src.agent_tools  # noqa: F401 — break tool_parsing ↔ agent_tools cycle
    from src.tool_parsing import _parse_inline_write_file_lines
    prose = (
        "Write to 1.txt:\n"
        "- write_file file=1.txt content=1\n\n"
        "Write to 2.txt:\n"
        "- write_file file=2.txt content=2\n"
    )
    blocks = _parse_inline_write_file_lines(prose)
    assert len(blocks) == 2
    assert blocks[0].tool_type == "write_file"
    assert blocks[0].content.startswith("1.txt\n1")


def test_parse_tool_blocks_picks_up_inline_write_file():
    import src.agent_tools  # noqa: F401
    from src.tool_parsing import parse_tool_blocks
    text = "- write_file file=3.txt content=hello"
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert "3.txt" in blocks[0].content


def test_is_workspace_file_create_still_true():
    assert is_workspace_file_create_request(_USER_PROMPT)
