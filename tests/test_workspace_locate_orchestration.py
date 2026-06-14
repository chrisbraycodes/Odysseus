from src.action_intents import classify_tool_intent
from src.workspace_locate_intent import (
    is_workspace_locate_request,
    workspace_locate_system_note,
)
from src.workspace_locate_orchestration import (
    derive_grep_passes,
    parse_grep_evidence,
    rank_locate_candidates,
    synthesize_locate_answer,
    _parse_grep_paths,
)

_USER_WHICH_FILE = (
    "which file would you change to update the home page listed readme "
    "for the github repo?"
)
_USER_READ_FIND = (
    "read each file and figure out which one it is, don't read all of node_modules"
)
_USER_HOME_SITE = (
    "no this site lists the readme for a github repo on the web page "
    "what file is that on in this repo?"
)


def test_is_workspace_locate_request():
    assert is_workspace_locate_request(_USER_WHICH_FILE)
    assert is_workspace_locate_request(_USER_READ_FIND)
    assert is_workspace_locate_request(_USER_HOME_SITE)
    assert is_workspace_locate_request("where is the code that fetches github readme")
    assert not is_workspace_locate_request("make a txt file named 1.txt")


def test_locate_not_pure_analyze():
    assert not is_workspace_locate_request(
        "analyze this project and tell me what this website is about"
    )


def test_workspace_locate_system_note():
    note = workspace_locate_system_note(_USER_HOME_SITE)
    assert note is not None
    assert "grep" in note
    assert "NOT GitHub.com's README.md" in note


def test_locate_promotes_chat_to_agent():
    intent = classify_tool_intent(_USER_WHICH_FILE)
    assert intent.needs_tools
    assert intent.category == "workspace"


def test_derive_grep_passes_includes_github():
    passes = derive_grep_passes(_USER_HOME_SITE)
    patterns = [p for p, _ in passes]
    assert any("github" in p for p in patterns)


def test_parse_grep_paths():
    output = "src/App.tsx:12: fetchGithubReadme()\nsrc/App.tsx:40: readme\n"
    paths = _parse_grep_paths(output)
    assert paths == ["src/App.tsx"]


def test_parse_grep_paths_skips_node_modules():
    output = "node_modules/foo/index.js:1: github\nsrc/page.tsx:2: github\n"
    paths = _parse_grep_paths(output)
    assert paths == ["src/page.tsx"]


def test_parse_grep_evidence():
    output = "src/Home.tsx:42: fetchGithubReadme(url)\nsrc/Home.tsx:88: readme\n"
    ev = parse_grep_evidence(output)
    assert ev[0][0] == "src/Home.tsx"
    assert ev[0][1] == 42


def test_rank_locate_candidates_github_readme():
    evidence = [
        ("src/Home.tsx", 10, "const url = 'https://api.github.com/repos/x/readme'"),
        ("README.md", 1, "# Project"),
        ("src/utils.ts", 3, "github helper"),
    ]
    ranked = rank_locate_candidates(evidence, _USER_HOME_SITE)
    assert ranked[0][0] == "src/Home.tsx"
    assert ranked[0][1] > ranked[1][1]


def test_synthesize_locate_answer_confident():
    ranked = [
        (
            "src/pages/Home.tsx",
            28,
            ["src/pages/Home.tsx:15: fetch raw.githubusercontent.com/readme"],
        ),
    ]
    text, confident = synthesize_locate_answer(
        user_msg=_USER_HOME_SITE,
        ranked=ranked,
        files_read=["src/pages/Home.tsx"],
    )
    assert confident
    assert "src/pages/Home.tsx" in text
    assert "Evidence" in text


def test_synthesize_locate_answer_weak_returns_not_confident():
    ranked = [("misc.txt", 4, ["misc.txt:1: foo"])]
    _, confident = synthesize_locate_answer(
        user_msg="which file handles foo",
        ranked=ranked,
        files_read=[],
    )
    assert not confident
