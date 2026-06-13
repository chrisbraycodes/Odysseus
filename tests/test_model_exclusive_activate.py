"""Tests for exclusive local model activation helpers."""

from src.model_exclusive_activate import (
    _model_names_match,
    _presets_for_model,
    _pick_preset,
    _task_is_local_active_serve,
)


def test_model_names_match_short_and_full():
    assert _model_names_match("meta-llama/Llama-3-8B", "Llama-3-8B")
    assert _model_names_match("Llama-3-8B", "meta-llama/Llama-3-8B")
    assert not _model_names_match("qwen2.5", "llama3")


def test_presets_for_model():
    presets = [
        {"name": "qwen-vllm", "model": "Qwen/Qwen2.5-7B-Instruct", "cmd": "vllm serve ..."},
        {"name": "llama", "model": "meta-llama/Llama-3-8B", "cmd": "vllm serve ..."},
    ]
    hits = _presets_for_model(presets, "Qwen2.5-7B-Instruct")
    assert len(hits) == 1
    assert hits[0]["name"] == "qwen-vllm"


def test_pick_preset_by_port():
    presets = [
        {"name": "a", "model": "m", "cmd": "vllm serve m --port 8001", "port": "8001"},
        {"name": "b", "model": "m", "cmd": "vllm serve m --port 8002", "port": "8002"},
    ]
    picked = _pick_preset(presets, "http://127.0.0.1:8002/v1")
    assert picked["name"] == "b"


def test_task_is_local_active_serve():
    assert _task_is_local_active_serve({"type": "serve", "status": "running", "remoteHost": ""})
    assert not _task_is_local_active_serve({"type": "serve", "status": "running", "remoteHost": "gpu-box"})
    assert not _task_is_local_active_serve({"type": "download", "status": "running", "remoteHost": ""})
    assert not _task_is_local_active_serve({"type": "serve", "status": "stopped", "remoteHost": ""})
