"""Route agent-mode tool turns to a stronger model when configured or available."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_Candidate = Tuple[str, str, Optional[Dict]]
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.I)

# Model-name hints that support OpenAI-style native tool calling.
_TOOL_CAPABLE_HINTS = (
    "gpt-4", "gpt-5", "claude", "gemini", "qwen3", "qwen2.5", "mixtral",
    "mistral", "llama-3.1", "llama-3.2", "llama-3.3", "llama-4",
    "deepseek-v", "deepseek-chat", "minimax", "kimi", "phi-3", "phi-4",
    "command-r", "glm-4", "hermes",
)


def model_param_size_b(model_name: str) -> float:
    """Extract approximate parameter count from a model id (e.g. 7B, 14B)."""
    m = _SIZE_RE.search(model_name or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def model_supports_native_tools(model_name: str) -> bool:
    lc = (model_name or "").lower()
    if "deepseek-r1" in lc:
        return False
    return any(h in lc for h in _TOOL_CAPABLE_HINTS)


def _endpoint_models(endpoint_id: str, owner: Optional[str]) -> List[str]:
    if not endpoint_id:
        return []
    try:
        from core.database import ModelEndpoint, SessionLocal
        from src.endpoint_resolver import _endpoint_enabled_models

        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == endpoint_id,
                ModelEndpoint.is_enabled == True,
            )
            if owner:
                from src.auth_helpers import owner_filter
                ep = owner_filter(q, ModelEndpoint, owner).first()
            else:
                ep = q.first()
            if not ep:
                return []
            return list(_endpoint_enabled_models(ep))
        finally:
            db.close()
    except Exception as e:
        logger.debug("agent_model_router: endpoint model list failed: %s", e)
        return []


def _pick_stronger_on_endpoint(
    endpoint_id: str,
    current_model: str,
    owner: Optional[str],
    *,
    min_b: float = 14.0,
) -> Optional[str]:
    models = _endpoint_models(endpoint_id, owner)
    if not models:
        return None
    current_size = model_param_size_b(current_model)
    if current_size >= min_b:
        return None
    stronger = [
        m for m in models
        if model_param_size_b(m) >= min_b and model_supports_native_tools(m)
    ]
    if not stronger:
        stronger = [m for m in models if model_param_size_b(m) >= min_b]
    if not stronger:
        return None
    return max(stronger, key=model_param_size_b)


def resolve_agent_fallback_candidates(owner: Optional[str] = None) -> List[_Candidate]:
    """Configured agent-model fallback chain."""
    try:
        from src.settings import get_user_setting, load_settings
        from src.endpoint_resolver import resolve_endpoint_by_id

        settings = load_settings()
        owner_str = owner or ""

        def _stg(key: str) -> str:
            return (get_user_setting(key, owner_str, settings.get(key, "")) or "").strip()

        raw = _stg("agent_model_fallbacks")
        if not raw:
            return []
        entries = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(entries, list):
            return []
        out: List[_Candidate] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            resolved = resolve_endpoint_by_id(entry.get("endpoint_id", ""), entry.get("model", ""), owner=owner)
            if resolved:
                out.append(resolved)
        return out
    except Exception as e:
        logger.debug("resolve_agent_fallback_candidates: %s", e)
        return []


def resolve_agent_model(
    session_url: str,
    session_model: str,
    session_headers: Optional[Dict],
    *,
    owner: Optional[str] = None,
    fallbacks: Optional[List[_Candidate]] = None,
    tools_needed: bool = True,
    min_param_b: float = 14.0,
) -> Tuple[str, str, Optional[Dict], List[_Candidate], str]:
    """Pick endpoint/model for agent mode when tools are needed.

    Returns (url, model, headers, fallbacks, reason).
    Chat sessions keep their model for plain chat; agent tool turns may upgrade.
    """
    if not tools_needed:
        return session_url, session_model, session_headers, fallbacks or [], "chat"

    try:
        from src.settings import get_user_setting, load_settings
        from src.endpoint_resolver import resolve_endpoint, resolve_endpoint_by_id

        settings = load_settings()
        owner_str = owner or ""

        def _stg(key: str) -> str:
            return (get_user_setting(key, owner_str, settings.get(key, "")) or "").strip()

        agent_ep = _stg("agent_endpoint_id")
        agent_model = _stg("agent_model")

        if agent_ep:
            resolved = resolve_endpoint_by_id(agent_ep, agent_model or None, owner=owner)
            if resolved:
                agent_fallbacks = resolve_agent_fallback_candidates(owner)
                logger.info(
                    "[agent-router] using configured agent model %s (session had %s)",
                    resolved[1], session_model,
                )
                return resolved[0], resolved[1], resolved[2], agent_fallbacks or fallbacks or [], "agent_setting"

        # Auto-upgrade on same endpoint when session model is small (e.g. 7B → 14B+).
        session_ep = _stg("default_endpoint_id")
        try:
            from core.database import ModelEndpoint, SessionLocal
            from src.endpoint_resolver import endpoint_lookup_keys

            db = SessionLocal()
            try:
                for key in endpoint_lookup_keys(session_url):
                    ep = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == key).first()
                    if ep:
                        session_ep = ep.id
                        break
            finally:
                db.close()
        except Exception:
            pass

        stronger = _pick_stronger_on_endpoint(session_ep, session_model, owner, min_b=min_param_b)
        if stronger and stronger != session_model:
            logger.info(
                "[agent-router] auto-upgraded %s → %s on endpoint %s",
                session_model, stronger, session_ep,
            )
            return session_url, stronger, session_headers, fallbacks or [], "auto_upgrade"

        # Try first fallback entry that is larger / tool-capable.
        for url, model, headers in fallbacks or []:
            if model_param_size_b(model) >= min_param_b and model_supports_native_tools(model):
                logger.info("[agent-router] using fallback model %s for tools", model)
                return url, model, headers, fallbacks or [], "fallback_upgrade"

    except Exception as e:
        logger.warning("[agent-router] resolution failed: %s", e)

    return session_url, session_model, session_headers, fallbacks or [], "session"
