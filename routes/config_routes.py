"""App configuration routes — settings, features, integrations (no auth)."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from src.integrations import (
    INTEGRATION_PRESETS,
    add_integration,
    delete_integration,
    execute_api_call,
    get_integration,
    load_integrations,
    mask_integration_secret,
    migrate_from_settings,
    update_integration,
)
from src.settings import (
    DEFAULT_SETTINGS,
    load_features as _load_features,
    load_settings as _load_settings,
    save_features as _save_features,
    save_settings as _save_settings,
)

logger = logging.getLogger(__name__)


def setup_config_routes() -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["config"])

    migrate_from_settings()

    @router.get("/features")
    async def get_features():
        return _load_features()

    @router.post("/features")
    async def set_features(request: Request):
        body = await request.json()
        current = _load_features()
        for key in current:
            if key in body and isinstance(body[key], bool):
                current[key] = body[key]
        _save_features(current)
        return current

    @router.get("/settings")
    async def get_settings():
        return _load_settings()

    @router.post("/settings")
    async def set_settings(request: Request):
        body = await request.json()
        current = _load_settings()
        _INT_RANGES = {
            "agent_max_rounds": (1, 200),
            "agent_max_tool_calls": (0, 1000),
        }
        for key in DEFAULT_SETTINGS:
            if key not in body:
                continue
            val = body[key]
            if key in _INT_RANGES:
                lo, hi = _INT_RANGES[key]
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{key} must be an integer")
                val = max(lo, min(val, hi))
            current[key] = val
        _save_settings(current)
        return current

    @router.get("/integrations")
    async def list_integrations_route():
        items = load_integrations()
        return {"integrations": [mask_integration_secret(item) for item in items]}

    @router.get("/integrations/presets")
    async def list_presets():
        return {
            "presets": {
                k: {kk: vv for kk, vv in v.items() if kk != "api_key"}
                for k, v in INTEGRATION_PRESETS.items()
            }
        }

    @router.post("/integrations")
    async def create_integration(request: Request):
        body = await request.json()
        item = add_integration(body)
        return {"ok": True, "integration": mask_integration_secret(item)}

    @router.put("/integrations/{integration_id}")
    async def update_integration_route(integration_id: str, request: Request):
        body = await request.json()
        item = update_integration(integration_id, body)
        if not item:
            raise HTTPException(404, "Integration not found")
        return {"ok": True, "integration": mask_integration_secret(item)}

    @router.delete("/integrations/{integration_id}")
    async def delete_integration_route(integration_id: str):
        ok = delete_integration(integration_id)
        if not ok:
            raise HTTPException(404, "Integration not found")
        return {"ok": True}

    @router.post("/integrations/{integration_id}/test")
    async def test_integration_route(integration_id: str):
        integ = get_integration(integration_id)
        if not integ:
            raise HTTPException(404, "Integration not found")
        preset = (integ.get("preset") or integ.get("name", "")).lower()

        if preset == "ntfy":
            from urllib.parse import urlparse

            raw_base = (integ.get("base_url") or "").strip()
            parsed = urlparse(raw_base)
            base = (
                f"{parsed.scheme}://{parsed.netloc}"
                if parsed.scheme and parsed.netloc
                else raw_base.rstrip("/")
            )
            settings = _load_settings()
            topic = (settings.get("reminder_ntfy_topic") or "reminders").strip() or "reminders"
            full_url = f"{base}/{topic}"
            api_key = integ.get("api_key", "")
            auth_type = (integ.get("auth_type") or "none").lower()
            headers = {
                "Title": "Odysseus connectivity test",
                "Tags": "white_check_mark",
                "Priority": "default",
            }
            if api_key:
                if auth_type == "bearer":
                    headers["Authorization"] = f"Bearer {api_key}"
                elif auth_type == "header":
                    headers[integ.get("auth_header") or "Authorization"] = api_key
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(
                        full_url,
                        content="Connectivity test from Odysseus. If you see this on your phone, ntfy is wired up correctly.",
                        headers=headers,
                    )
                if r.is_success:
                    return {
                        "ok": True,
                        "message": (
                            f"Sent to {full_url} — on your ntfy app, "
                            f"subscribe to topic \"{topic}\" with server "
                            f"\"{base}\" (or paste the full URL: {full_url})."
                        ),
                    }
                return {"ok": False, "message": f"ntfy returned HTTP {r.status_code} from {full_url}: {r.text[:200]}"}
            except Exception as e:
                hint = ""
                if parsed.hostname not in ("127.0.0.1", "localhost"):
                    hint = " If this is Docker Compose ntfy, set NTFY_BIND to that host/Tailscale IP and NTFY_BASE_URL to the same server URL in .env, then recreate ntfy."
                return {"ok": False, "message": f"ntfy publish to {full_url} failed: {e}.{hint}"[:500]}

        if preset == "discord_webhook":
            webhook_url = (integ.get("base_url") or "").strip()
            if not webhook_url:
                return {"ok": False, "message": "No webhook URL set — paste the full Discord webhook URL into the Base URL field."}
            payload = {
                "embeds": [{
                    "title": "Odysseus connectivity test",
                    "description": "If you see this, your Discord Webhook integration is wired up correctly.",
                    "color": 5793266,
                }]
            }
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(webhook_url, json=payload)
                if r.is_success:
                    return {"ok": True, "message": "Test embed sent — check your Discord channel to confirm it arrived."}
                return {"ok": False, "message": f"Discord returned HTTP {r.status_code}: {r.text[:200]}"}
            except Exception as e:
                return {"ok": False, "message": f"Request failed: {e}"[:400]}

        health_paths = {
            "miniflux": "/v1/me",
            "gitea": "/api/v1/version",
            "linkding": "/api/tags/",
            "homeassistant": "/api/",
            "home assistant": "/api/",
        }
        path = health_paths.get(preset, "/")
        result = await execute_api_call(integration_id, "GET", path)
        if result.get("exit_code", 1) == 0:
            return {"ok": True, "message": "Connection successful"}
        return {"ok": False, "message": (result.get("error") or "Connection failed")[:300]}

    return router
