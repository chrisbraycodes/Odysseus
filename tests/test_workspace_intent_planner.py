from src.workspace_intent_planner import _parse_planner_json


def test_parse_planner_json_fenced():
    raw = '''Here is the plan:
```json
{"files": [{"path": "1.txt", "content": "10"}, {"path": "2.txt", "content": "9"}]}
```
'''
    specs = _parse_planner_json(raw)
    assert specs == [("1.txt", "10"), ("2.txt", "9")]


def test_parse_planner_json_bare_object():
    raw = '{"files": [{"path": "a.txt", "content": "hello"}]}'
    assert _parse_planner_json(raw) == [("a.txt", "hello")]


def test_parse_planner_json_rejects_absolute_paths():
    raw = '{"files": [{"path": "/etc/passwd", "content": "x"}]}'
    assert _parse_planner_json(raw) is None
