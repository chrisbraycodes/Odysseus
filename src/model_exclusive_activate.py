"""Exclusive local model activation for the model picker.

When the user selects a local model, stop other container-local cookbook
serves (free GPU/RAM) and launch a saved preset if the endpoint is down.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from core.database import ModelEndpoint, SessionLocal
from routes.model_routes import (
    _classify_endpoint,
    _effective_endpoint_kind,
    _normalize_base,
    _probe_endpoint,
)

logger = logging.getLogger(__name__)

_COOKBOOK_STATE_PATH = Path(os.environ.get("DATA_DIR", "data")) / "cookbook_state.json"
_ACTIVE_SERVE_STATUSES = frozenset({"running", "ready", "starting", "idle"})


def _model_names_match(a: str, b: str) -> bool:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    a_short = a.split("/")[-1]
    b_short = b.split("/")[-1]
    return a_short == b_short or a.endswith("/" + b) or b.endswith("/" + a)


def _presets_for_model(presets: List[Any], model_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in presets or []:
        if not isinstance(p, dict):
            continue
        pm = p.get("model") or p.get("modelId") or ""
        pn = p.get("name") or ""
        if _model_names_match(pm, model_id) or _model_names_match(pn, model_id):
            out.append(p)
    return out


def _pick_preset(presets: List[Dict[str, Any]], endpoint_url: str = "") -> Optional[Dict[str, Any]]:
    if not presets:
        return None
    port = ""
    if endpoint_url:
        try:
            p = urlparse(endpoint_url).port
            if p:
                port = str(p)
        except Exception:
            pass
    if port:
        for p in presets:
            cmd = (p.get("cmd") or "")
            pm = re.search(r"--port[=\s]+(\d+)", cmd) or re.search(r"(?:^|\s)-p[=\s]+(\d+)", cmd)
            if pm and pm.group(1) == port:
                return p
            if str(p.get("port") or "") == port:
                return p
    confirmed = [p for p in presets if p.get("confirmedWorking")]
    if confirmed:
        return confirmed[0]
    return presets[0]


def _load_cookbook_state() -> Dict[str, Any]:
    if not _COOKBOOK_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(_COOKBOOK_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("model_exclusive_activate: failed to read cookbook state: %s", e)
        return {}


def _task_is_local_active_serve(task: Dict[str, Any]) -> bool:
    if task.get("type") != "serve":
        return False
    if (task.get("remoteHost") or "").strip():
        return False
    status = (task.get("status") or "").lower()
    if status in ("stopped", "done", "cancelled", "crashed", "failed"):
        return False
    if status in _ACTIVE_SERVE_STATUSES:
        return True
    return status not in ("stopped", "done", "cancelled", "crashed", "failed")


def _task_model_id(task: Dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    return str(payload.get("repo_id") or task.get("name") or "")


def _endpoint_has_model(base_url: str, api_key: Optional[str], model_id: str) -> bool:
    try:
        models = _probe_endpoint(base_url, api_key, timeout=3)
        return any(_model_names_match(m, model_id) for m in (models or []))
    except Exception:
        return False


def _ollama_root(base_url: str) -> Optional[str]:
    base = _normalize_base(base_url or "")
    if "ollama" not in base.lower() and ":11434" not in base:
        return None
    try:
        parsed = urlparse(base)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        return f"http://{host}:{port}"
    except Exception:
        return None


async def _ollama_unload_others(base_url: str, keep_model: str) -> List[str]:
    root = _ollama_root(base_url)
    if not root:
        return []
    unloaded: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{root}/api/ps")
            if r.status_code >= 400:
                return []
            models = (r.json() or {}).get("models") or []
            for entry in models:
                name = (entry or {}).get("name") or ""
                if not name or _model_names_match(name, keep_model):
                    continue
                try:
                    await client.post(
                        f"{root}/api/generate",
                        json={"model": name, "prompt": "", "keep_alive": 0},
                    )
                    unloaded.append(name)
                except Exception as e:
                    logger.debug("ollama unload %s failed: %s", name, e)
    except Exception as e:
        logger.debug("ollama ps failed: %s", e)
    return unloaded


async def _launch_preset(preset: Dict[str, Any]) -> Tuple[bool, str, str]:
    from src.tool_implementations import (
        _COOKBOOK_BASE,
        _cookbook_env_for_host,
        _cookbook_register_task,
        _internal_headers,
    )

    repo_id = preset.get("model") or preset.get("modelId") or ""
    cmd = (preset.get("cmd") or "").strip()
    host = preset.get("host") or preset.get("remoteHost") or ""
    if not repo_id or not cmd:
        return False, "", "Preset is missing model or cmd"

    payload: Dict[str, Any] = {"repo_id": repo_id, "cmd": cmd}
    if host:
        payload["remote_host"] = host
    env_cfg = await _cookbook_env_for_host(host)
    for key, src in (
        ("env_prefix", "env_prefix"),
        ("gpus", "gpus"),
        ("hf_token", "hf_token"),
        ("platform", "platform"),
        ("ssh_port", "ssh_port"),
    ):
        if env_cfg.get(src):
            payload[key if key != "ssh_port" else "ssh_port"] = env_cfg[src]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_COOKBOOK_BASE}/api/model/serve",
                json=payload,
                headers=_internal_headers(),
            )
            data = resp.json() if resp.content else {}
        if not data.get("ok"):
            err = data.get("error") or data.get("detail") or "Serve failed"
            return False, "", str(err)
        sid = str(data.get("session_id") or "")
        await _cookbook_register_task(
            session_id=sid,
            model=repo_id,
            host=host,
            cmd=cmd,
            task_type="serve",
        )
        return True, sid, ""
    except Exception as e:
        return False, "", str(e)


async def _stop_local_serve(session_id: str) -> bool:
    from src.tool_implementations import _cookbook_kill_session

    result = await _cookbook_kill_session(session_id, verb="Stopped server")
    return result.get("exit_code") == 0


def _fallback_cmd_from_tasks(tasks: List[Any], model_id: str) -> Optional[Dict[str, str]]:
    for task in reversed(tasks or []):
        if not isinstance(task, dict) or task.get("type") != "serve":
            continue
        if not _model_names_match(_task_model_id(task), model_id):
            continue
        cmd = ((task.get("payload") or {}).get("_cmd") or "").strip()
        if cmd:
            return {
                "model": _task_model_id(task),
                "cmd": cmd,
                "host": task.get("remoteHost") or "",
                "remoteHost": task.get("remoteHost") or "",
            }
    return None


async def activate_model_exclusive(
    model_id: str,
    *,
    endpoint_id: Optional[str] = None,
    endpoint_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Stop other container-local serves and ensure *model_id* is available."""
    model_id = (model_id or "").strip()
    if not model_id:
        return {"ok": False, "error": "model_id is required"}

    db = SessionLocal()
    try:
        ep: Optional[ModelEndpoint] = None
        if endpoint_id:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
        if ep is None and endpoint_url:
            norm = _normalize_base(endpoint_url)
            for row in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():
                if _normalize_base(row.base_url) == norm:
                    ep = row
                    break
    finally:
        db.close()

    base_url = _normalize_base(endpoint_url or (ep.base_url if ep else ""))
    if not base_url:
        return {"ok": True, "skipped": True, "reason": "no_endpoint"}

    kind = _effective_endpoint_kind(ep, base_url) if ep else "auto"
    if _classify_endpoint(base_url, kind) != "local":
        return {"ok": True, "skipped": True, "reason": "cloud_endpoint"}

    api_key = getattr(ep, "api_key", None) if ep else None
    already_online = _endpoint_has_model(base_url, api_key, model_id)

    state = _load_cookbook_state()
    tasks = state.get("tasks") or []
    stopped: List[str] = []
    kept: Optional[str] = None

    for task in tasks:
        if not isinstance(task, dict) or not _task_is_local_active_serve(task):
            continue
        sid = task.get("sessionId") or task.get("id") or ""
        if not sid:
            continue
        if _model_names_match(_task_model_id(task), model_id):
            kept = sid
            continue
        if await _stop_local_serve(sid):
            stopped.append(sid)

    ollama_unloaded: List[str] = []
    if _ollama_root(base_url):
        ollama_unloaded = await _ollama_unload_others(base_url, model_id)

    if kept and not already_online:
        if await _stop_local_serve(kept):
            stopped.append(kept)
        kept = None

    if already_online:
        return {
            "ok": True,
            "action": "exclusive_cleanup",
            "already_online": True,
            "stopped_sessions": stopped,
            "kept_session": kept,
            "ollama_unloaded": ollama_unloaded,
        }

    presets = _presets_for_model(state.get("presets") or [], model_id)
    preset = _pick_preset(presets, base_url)
    launch_spec = preset or _fallback_cmd_from_tasks(tasks, model_id)

    if not launch_spec:
        return {
            "ok": False,
            "error": (
                f"The model server for {model_id} is not running. "
                "Start it from Cookbook → Serve (pick the model, choose vLLM or llama.cpp, "
                "click Launch, then Save the config). Or start your existing server on the "
                f"host and pick the matching endpoint in the model list."
            ),
            "stopped_sessions": stopped,
            "needs_preset": True,
            "endpoint_url": base_url,
        }

    started, session_id, err = await _launch_preset(launch_spec)
    if not started:
        return {
            "ok": False,
            "error": err or "Failed to start model",
            "stopped_sessions": stopped,
        }

    return {
        "ok": True,
        "action": "started",
        "already_online": False,
        "starting": True,
        "session_id": session_id,
        "stopped_sessions": stopped,
        "kept_session": kept,
        "ollama_unloaded": ollama_unloaded,
        "preset": (preset or {}).get("name") or model_id,
    }
