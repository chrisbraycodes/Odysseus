import os

from src.workspace_index import (
    _GitignoreRules,
    _iter_workspace_files,
    _should_index_file,
    workspace_key,
)


def test_workspace_key_stable():
    a = workspace_key("/tmp/myproject")
    b = workspace_key("/tmp/myproject")
    assert a == b
    assert len(a) == 16


def test_should_index_file_extensions():
    gi = _GitignoreRules()
    assert _should_index_file("src/app.py", "app.py", gi)
    assert _should_index_file("README.md", "README.md", gi)
    assert not _should_index_file("img/logo.png", "logo.png", gi)


def test_gitignore_skips_pattern(tmp_path):
    gi = _GitignoreRules()
    ignore = tmp_path / ".gitignore"
    ignore.write_text("*.log\nbuild/\n", encoding="utf-8")
    gi.add_file(str(ignore))
    assert gi.ignored("debug.log")
    assert gi.ignored("build/out.txt")


def test_iter_workspace_files_respects_skip_dirs(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("x", encoding="utf-8")
    rels = [rel for _, rel in _iter_workspace_files(str(tmp_path))]
    assert "app.py" in rels
    assert not any("node_modules" in r for r in rels)


def test_iter_workspace_files_cap(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"x={i}", encoding="utf-8")
    rels = [rel for _, rel in _iter_workspace_files(str(tmp_path))]
    assert len(rels) == 5


def test_format_search_hits_empty():
    from src.workspace_index import format_search_hits
    assert format_search_hits([]) == ""


def test_format_search_hits_includes_path():
    from src.workspace_index import format_search_hits
    text = format_search_hits([
        {"rel_path": "src/main.py", "similarity": 0.9, "document": "def main(): pass"},
    ])
    assert "src/main.py" in text
    assert "main" in text


def test_synthesize_analyze_summary_from_readme():
    from src.workspace_analyze_orchestration import synthesize_analyze_summary
    text = synthesize_analyze_summary(
        workspace="/proj",
        tree="  src/\n  README.md",
        files_read=["README.md", "package.json"],
        file_contents={
            "README.md": "# My App\n\nA local IDE for coding with LLMs.",
            "package.json": '{"name":"my-app","dependencies":{"react":"18"}}',
        },
    )
    assert "My App" in text or "local IDE" in text
    assert "react" in text


def test_cap_scan_context_truncates():
    from src.workspace_analyze_orchestration import cap_scan_context
    out = cap_scan_context("x" * 20000, max_chars=1000)
    assert len(out) <= 1000
    assert "truncated" in out
