from src.intent_index import IntentIndex, ACTION_UTTERANCES


def test_action_registry_has_core_workspace_actions():
    assert "create_files" in ACTION_UTTERANCES
    assert "analyze_repo" in ACTION_UTTERANCES
    assert "locate_code" in ACTION_UTTERANCES


def test_keyword_fallback_create_files():
    idx = IntentIndex.__new__(IntentIndex)
    idx._healthy = True
    idx._lanes = []
    m = idx._keyword_match("please create txt files numbered with content inside")
    assert m is not None
    assert m.action == "create_files"
    assert m.source == "keyword"


def test_semantic_action_match_without_index():
    from src.intent_index import semantic_action_match
    # No Chroma/embeddings in unit test env — should not raise.
    assert semantic_action_match("create files", "create_files") in (True, False)
