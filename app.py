# app.py — slim orchestrator
import mimetypes
import os


def register_static_mime_types() -> None:
    """Force stable JS module MIME types across platforms.

    Some native Windows setups inherit stale/incorrect registry mappings for
    ``.js``/``.mjs``, which can make Starlette serve ES modules with a non-JS
    ``Content-Type`` and cause the UI to load but fail on click. Re-register the
    standard MIME types at startup so static assets are served consistently.
    """

    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")


register_static_mime_types()

# Windows: force HuggingFace/fastembed to COPY model files instead of symlinking.
# On a network-share/UNC data dir Windows can't follow HF's symlinks ([WinError
# 1463]), so the ONNX embedding model fails to load. huggingface_hub reads this
# at import time, so set it before anything pulls it in. (Mirrored in
# src/embeddings.py for non-server entrypoints.)
if os.name == "nt":
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from dotenv import load_dotenv
# encoding="utf-8-sig" tolerates a UTF-8 BOM in .env — a common Windows gotcha
# when the file is saved from Notepad. Without this, the first key parses as
# "﻿AUTH_ENABLED" instead of "AUTH_ENABLED", so AUTH_ENABLED=false (etc.)
# is silently ignored and the user is unexpectedly forced to log in (issue #142).
# utf-8-sig reads plain UTF-8 (no BOM) identically, so this is safe everywhere.
load_dotenv(encoding="utf-8-sig")

import asyncio
import logging
import secrets
from datetime import datetime
from typing import Dict

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# Core imports
from core.constants import (
    BASE_DIR, STATIC_DIR, SESSIONS_FILE,
    REQUEST_TIMEOUT, OPENAI_API_KEY,
)
from core.database import SessionLocal
from core.middleware import SecurityHeadersMiddleware
from core.exceptions import (
    SessionNotFoundError, InvalidFileUploadError,
    LLMServiceError, WebSearchError,
)

from src.app_helpers import abs_join
from src.generated_images import GENERATED_IMAGE_HEADERS, resolve_generated_image_path

# ========= LOGGING =========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# ========= APP =========
# Lifespan is defined below (after all helpers it references are in scope)
# and passed to FastAPI so we can use the modern context-manager lifecycle
# instead of the deprecated @app.on_event("startup"/"shutdown") decorators.
app = FastAPI(
    title="AI Chat Application",
    description="Comprehensive AI chat with memory, research, and multi-modal capabilities",
    version="1.0.0",
)

# ========= CORS =========
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Auth-Token",
        "X-Odysseus-Internal-Token",
        "X-Odysseus-Owner",
        "X-Requested-With",
        "X-TZ-Offset",
    ],
)

# ========= SECURITY HEADERS MIDDLEWARE =========
app.add_middleware(SecurityHeadersMiddleware)


# ========= REQUEST TIMEOUT (FALLBACK FOR HUNG HANDLERS) =========
# If a single request takes longer than REQUEST_HARD_TIMEOUT, abort it and
# return 504 instead of holding the event loop hostage. Whitelisted paths
# (streaming, long-running shell exec, research) are exempt because they
# legitimately stay open. Without this, a single hung subprocess.run or
# missing-timeout httpx call locks up the entire server for everyone.
import asyncio as _asyncio
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.responses import JSONResponse as _JSONResponse

REQUEST_HARD_TIMEOUT = float(os.getenv("REQUEST_HARD_TIMEOUT", "45"))
_TIMEOUT_EXEMPT_PREFIXES = (
    "/api/chat",            # streaming
    "/api/shell/stream",    # SSE
    "/api/research",        # multi-minute jobs
    "/api/model/download",  # tmux setup may run pip installs
    "/api/model/probe",     # SSE; iterates models with up to 8s timeout each
    "/api/model-endpoints", # /probe sub-route also iterates models
    "/api/cookbook/setup",  # remote pacman/apt installs
    "/api/upload",          # large files
    "/api/image",           # diffusion proxies (inpaint/harmonize/upscale/etc.) — own 120s httpx timeout
)


class _RequestTimeoutMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path or ""
        if any(path.startswith(p) for p in _TIMEOUT_EXEMPT_PREFIXES):
            return await call_next(request)
        try:
            return await _asyncio.wait_for(call_next(request), timeout=REQUEST_HARD_TIMEOUT)
        except _asyncio.TimeoutError:
            return _JSONResponse(
                {"detail": f"Request exceeded {REQUEST_HARD_TIMEOUT:.0f}s timeout"},
                status_code=504,
            )


app.add_middleware(_RequestTimeoutMiddleware)

# ========= STATIC FILES =========
os.makedirs(STATIC_DIR, exist_ok=True)


class _RevalidatingStatic(StaticFiles):
    """Serve static assets normally, but force the browser to REVALIDATE
    source files (.js/.css/.html) on every load instead of serving a stale
    copy from disk cache. The app ships raw ES modules with no build step or
    versioned URLs, so browsers were caching modules across deploys — a code
    change wouldn't appear without a manual hard-refresh. `no-cache` keeps the
    cached bytes but requires a conditional request; unchanged files still
    return a cheap 304 (ETag/Last-Modified are preserved)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _RevalidatingStatic(directory="static"), name="static")

# ========= GENERATED IMAGES =========
@app.get("/api/generated-image/{filename}")
async def serve_generated_image(filename: str, request: Request):
    """Serve generated images from the data directory."""
    img_path = resolve_generated_image_path(filename)
    ext = filename.rsplit('.', 1)[-1].lower()
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
        "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
        "mkv": "video/x-matroska", "m4v": "video/mp4",
    }.get(ext, "application/octet-stream")
    # Generated-image filenames are content hashes → the bytes for a given
    # filename never change. Cache them hard so the gallery doesn't
    # re-download every full-size image each time it's opened. `immutable`
    # tells the browser it never needs to revalidate within the max-age.
    return FileResponse(
        str(img_path),
        media_type=mime,
        headers=GENERATED_IMAGE_HEADERS,
    )

# ========= YOUTUBE INIT =========
from services.youtube import init_youtube
init_youtube()

# ========= RAG (vector document RAG) =========
# VectorRAG (ChromaDB-backed personal-document semantic search). Initialized
# lazily via get_rag_manager() — returns None if ChromaDB isn't reachable
# (no server running on the configured host:port), in which case personal-doc
# routes return a clean 503 instead of busy-retrying every request.
#
# Note: this was previously hardcoded off because chromadb 1.4.1 / pydantic
# 2.12 were mutually incompatible at the time. With the current pins
# (chromadb 1.5.x + pydantic 2.13.x) the init works and Personal Docs
# (POST /api/personal/add_directory etc.) is functional again.
from src.rag_singleton import get_rag_manager
rag_manager = get_rag_manager()
rag_available = rag_manager is not None
if rag_available:
    logger.info("Vector document RAG initialized")
else:
    logger.info(
        "Vector document RAG not available at startup "
        "(ChromaDB may not be reachable yet — routes will retry lazily)"
    )

# ========= IMPORT CONFIG =========
from src.config import config

# ========= COMPONENT INITIALIZATION =========
from src.app_initializer import initialize_managers

components = initialize_managers(BASE_DIR, rag_manager)

session_manager   = components["session_manager"]
from src.assistant_log import set_session_manager as _set_asst_sm
_set_asst_sm(session_manager)
memory_manager    = components["memory_manager"]
memory_vector     = components.get("memory_vector")
upload_handler    = components["upload_handler"]
personal_docs_mgr = components["personal_docs_manager"]
api_key_manager   = components["api_key_manager"]
preset_manager    = components["preset_manager"]
chat_processor    = components["chat_processor"]
research_handler  = components["research_handler"]
chat_handler      = components["chat_handler"]
model_discovery   = components["model_discovery"]
skills_manager    = components["skills_manager"]

# TTS
from services.tts import get_tts_service

tts_service = get_tts_service()
logger.info("TTS service initialized (provider managed via admin settings)")

# ========= EXCEPTION HANDLERS =========
@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(status_code=404, content={"error": "SESSION_NOT_FOUND", "message": str(exc)})

@app.exception_handler(InvalidFileUploadError)
async def invalid_file_upload_handler(request: Request, exc: InvalidFileUploadError):
    return JSONResponse(status_code=400, content={"error": "INVALID_FILE_UPLOAD", "message": str(exc)})

@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError):
    return JSONResponse(status_code=502, content={"error": "LLM_SERVICE_ERROR", "message": str(exc)})

@app.exception_handler(WebSearchError)
async def web_search_error_handler(request: Request, exc: WebSearchError):
    return JSONResponse(status_code=502, content={"error": "WEB_SEARCH_ERROR", "message": str(exc)})

# ========= WEBHOOK MANAGER =========
from src.webhook_manager import WebhookManager

webhook_manager = WebhookManager(api_key_manager=api_key_manager)

# ========= INCLUDE ROUTERS =========

# Config (settings, features, integrations)
from routes.config_routes import setup_config_routes
app.include_router(setup_config_routes())

# Uploads
from routes.upload_routes import setup_upload_routes
upload_router, upload_cleanup_func = setup_upload_routes(upload_handler)
app.include_router(upload_router)
upload_cleanup_task = None

# Emoji SVG proxy (same-origin, lazy-cached Twemoji) — lets the chat render
# emojis as flat SVG instead of system color glyphs.
from routes.emoji_routes import setup_emoji_routes
app.include_router(setup_emoji_routes())

from routes.workspace_routes import setup_workspace_routes
app.include_router(setup_workspace_routes())

# Sessions
from routes.session_routes import setup_session_routes
session_config = {"REQUEST_TIMEOUT": REQUEST_TIMEOUT, "OPENAI_API_KEY": OPENAI_API_KEY, "SESSIONS_FILE": SESSIONS_FILE}
app.include_router(setup_session_routes(session_manager, session_config, webhook_manager=webhook_manager))

# Admin Danger Zone wipes (Settings → System → Danger Zone)
from routes.admin_wipe_routes import setup_admin_wipe_routes
app.include_router(setup_admin_wipe_routes(session_manager))

# Memory
from routes.memory_routes import setup_memory_routes
memory_router = setup_memory_routes(memory_manager, session_manager, memory_vector=memory_vector)
app.include_router(memory_router)
from routes.skills_routes import setup_skills_routes
app.include_router(setup_skills_routes(skills_manager))

# Chat
from routes.chat_routes import setup_chat_routes
app.include_router(setup_chat_routes(
    session_manager, chat_handler, chat_processor,
    memory_manager, research_handler, upload_handler,
    memory_vector=memory_vector,
    webhook_manager=webhook_manager,
    skills_manager=skills_manager,
))

# Research (background deep-research tasks)
from routes.research_routes import setup_research_routes
app.include_router(setup_research_routes(research_handler, session_manager=session_manager))

# History
from routes.history_routes import setup_history_routes
app.include_router(setup_history_routes(session_manager))

# Search
from routes.search_routes import setup_search_routes
app.include_router(setup_search_routes(config))

# Presets
from routes.preset_routes import setup_preset_routes
app.include_router(setup_preset_routes(preset_manager))

# Diagnostics
from routes.diagnostics_routes import setup_diagnostics_routes
app.include_router(setup_diagnostics_routes(rag_manager, rag_available, research_handler))

# Cleanup
from routes.cleanup_routes import setup_cleanup_routes
app.include_router(setup_cleanup_routes(session_manager))

# Personal docs
from routes.personal_routes import setup_personal_routes
app.include_router(setup_personal_routes(personal_docs_mgr, rag_manager, rag_available))

# Embedding model management
from routes.embedding_routes import setup_embedding_routes
app.include_router(setup_embedding_routes())

# Models
from routes.model_routes import setup_model_routes
app.include_router(setup_model_routes(model_discovery))

# GitHub Copilot device-flow login
from routes.copilot_routes import setup_copilot_routes
app.include_router(setup_copilot_routes())

# TTS
from routes.tts_routes import setup_tts_routes
app.include_router(setup_tts_routes(tts_service))

# STT
from services.stt import get_stt_service
stt_service = get_stt_service()
from routes.stt_routes import setup_stt_routes
app.include_router(setup_stt_routes(stt_service))
logger.info("STT service initialized (provider managed via settings)")

# Documents (artifacts/canvas)
from routes.document_routes import setup_document_routes
document_router = setup_document_routes(session_manager, upload_handler)
app.include_router(document_router)

# Signatures (reusable image stamps)
from routes.signature_routes import setup_signature_routes
app.include_router(setup_signature_routes())

# Gallery (image library)
from routes.gallery_routes import setup_gallery_routes
app.include_router(setup_gallery_routes())

# Persisted image-editor drafts (server-backed projects)
from routes.editor_draft_routes import setup_editor_draft_routes
app.include_router(setup_editor_draft_routes())

# Scheduled tasks + event bus
from src.task_scheduler import TaskScheduler
task_scheduler = TaskScheduler(session_manager)
from src.event_bus import set_task_scheduler
set_task_scheduler(task_scheduler)
from routes.task_routes import setup_task_routes
app.include_router(setup_task_routes(task_scheduler))

from routes.assistant_routes import setup_assistant_routes
app.include_router(setup_assistant_routes(task_scheduler))

# Calendar (CalDAV)
from routes.calendar_routes import setup_calendar_routes
calendar_router = setup_calendar_routes()
app.include_router(calendar_router)

# Shell (user-facing command execution)
from routes.shell_routes import setup_shell_routes
app.include_router(setup_shell_routes())

# Workspace terminal (interactive PTY over WebSocket)
from routes.host_terminal_routes import setup_host_terminal_routes
from routes.terminal_routes import setup_terminal_routes
app.include_router(setup_host_terminal_routes())
app.include_router(setup_terminal_routes())

# Cookbook (model download/serve/cache, cookbook state sync)
from routes.cookbook_routes import setup_cookbook_routes
app.include_router(setup_cookbook_routes())

# Hardware model fitting (cookbook "What Fits?" tab)
from routes.hwfit_routes import setup_hwfit_routes
app.include_router(setup_hwfit_routes())

# Model A/B Comparison
from routes.compare_routes import setup_compare_routes
app.include_router(setup_compare_routes(session_manager))

# User Preferences
from routes.prefs_routes import setup_prefs_routes
app.include_router(setup_prefs_routes())

# Backup (export/import user data)
from routes.backup_routes import setup_backup_routes
app.include_router(setup_backup_routes(memory_manager, preset_manager, skills_manager))

from routes.font_routes import setup_font_routes
app.include_router(setup_font_routes())


# MCP (Model Context Protocol)
from src.mcp_manager import McpManager
from src.agent_tools import set_mcp_manager
from routes.mcp_routes import setup_mcp_routes

mcp_manager = McpManager()
set_mcp_manager(mcp_manager)
app.include_router(setup_mcp_routes(mcp_manager))
logger.info("MCP routes initialized")

# AI Interaction tools (debates, pipelines, self-managing AI, UI control)
from src.ai_interaction import set_session_manager as set_ai_session_manager, set_memory_manager as set_ai_memory_manager, set_rag_manager as set_ai_rag_manager
set_ai_session_manager(session_manager)
set_ai_memory_manager(memory_manager, memory_vector)
set_ai_rag_manager(rag_manager, personal_docs_mgr)
logger.info("AI interaction tools initialized (session, memory, RAG, UI control)")

# Webhooks
from routes.webhook_routes import setup_webhook_routes
app.include_router(setup_webhook_routes(webhook_manager, session_manager, api_key_manager))

logger.info("Webhook routes initialized")

# Notes (Google Keep-style notes/todos)
from routes.note_routes import setup_note_routes
app.include_router(setup_note_routes(task_scheduler))

# Email
from routes.email_routes import setup_email_routes
email_router = setup_email_routes()
app.include_router(email_router)

# Codex integration — HTTP surface for the Codex plugin/MCP bridge. Reuses
# api_token scopes (todos:read|write, email:read|draft|send) so external
# Codex sessions can only touch the data the user explicitly allowed. Mounted
# AFTER email so the codex_routes can borrow the email router for shared
# search/threading helpers.
from routes.codex_routes import setup_codex_routes, setup_claude_routes
app.include_router(setup_codex_routes(
    email_router=email_router,
    memory_router=memory_router,
    calendar_router=calendar_router,
    document_router=document_router,
))
app.include_router(setup_claude_routes())

from routes.vault_routes import setup_vault_routes
app.include_router(setup_vault_routes())

# Contacts (CardDAV)
from routes.contacts_routes import setup_contacts_routes
app.include_router(setup_contacts_routes())

from companion import setup_companion_routes
app.include_router(setup_companion_routes())

# ========= ROUTES (kept in app.py) =========

def _serve_html_with_nonce(request: Request, file_path: str) -> HTMLResponse:
    """Read an HTML file and inject the CSP nonce into inline <script> tags."""
    with open(file_path, "r", encoding="utf-8") as f:
        html = f.read()
    nonce = getattr(request.state, "csp_nonce", "")
    html = html.replace("{{CSP_NONCE}}", nonce)
    return HTMLResponse(html)

@app.get("/")
async def serve_index(request: Request):
    static_path = abs_join(BASE_DIR, "static/index.html")
    if os.path.exists(static_path):
        return _serve_html_with_nonce(request, static_path)
    root_path = abs_join(BASE_DIR, "index.html")
    if os.path.exists(root_path):
        return _serve_html_with_nonce(request, root_path)
    raise HTTPException(404, "index.html not found")

@app.get("/notes")
async def serve_notes(request: Request):
    return await serve_index(request)

@app.get("/calendar")
async def serve_calendar(request: Request):
    return await serve_index(request)

# Per-tool deep-link routes — all serve the same SPA, the JS auto-opens
# the matching modal based on window.location.pathname. Each route also
# gets a unique favicon + page title via inline script in index.html so
# bookmarks render with tool-specific icons.
@app.get("/cookbook")
async def serve_cookbook(request: Request):
    return await serve_index(request)

@app.get("/email")
async def serve_email(request: Request):
    return await serve_index(request)

@app.get("/memory")
async def serve_memory(request: Request):
    return await serve_index(request)

@app.get("/gallery")
async def serve_gallery(request: Request):
    return await serve_index(request)

@app.get("/tasks")
async def serve_tasks(request: Request):
    return await serve_index(request)

@app.get("/library")
async def serve_library(request: Request):
    return await serve_index(request)

@app.get("/workspace")
async def serve_workspace(request: Request):
    return await serve_index(request)

@app.get("/backgrounds")
async def serve_backgrounds(request: Request):
    """Sandbox page for prototyping background effects. No auth required."""
    return _serve_html_with_nonce(request, abs_join(BASE_DIR, "static/backgrounds.html"))

@app.get("/api/version")
async def get_version():
    from core.constants import APP_VERSION
    return {"version": APP_VERSION}

@app.get("/api/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/ready")
async def readiness_check() -> JSONResponse:
    """Readiness / integrity self-check — DB, data dir, local-first storage.

    Unlike /api/health (liveness), this returns 503 unless every critical
    subsystem is whole, so an orchestrator can gate traffic on real readiness.
    """
    from src.readiness import check_readiness
    result = check_readiness()
    return JSONResponse(status_code=200 if result.get("ready") else 503, content=result)

@app.get("/api/runtime")
async def runtime_info() -> Dict[str, object]:
    in_docker = os.path.exists("/.dockerenv")
    if not in_docker:
        try:
            with open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore") as fh:
                cg = fh.read()
            in_docker = any(marker in cg for marker in ("docker", "containerd", "kubepods"))
        except Exception:
            in_docker = False
    ollama_url = (
        os.getenv("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_URL")
        or ("http://host.docker.internal:11434/v1" if in_docker else "http://127.0.0.1:11434/v1")
    )
    return {
        "in_docker": in_docker,
        "ollama_base_url": ollama_url,
    }

# ========= LIFECYCLE =========

@asynccontextmanager
async def _lifespan(app):
    """Modern lifespan context manager replacing deprecated @app.on_event."""
    # ── STARTUP ──
    await _startup_event()
    yield
    # ── SHUTDOWN ──
    await _shutdown_event()

app.router.lifespan_context = _lifespan


async def _startup_event():
    global upload_cleanup_task
    logger.info("Application starting up...")
    webhook_manager.set_loop(asyncio.get_running_loop())
    # Wipe any leftover incognito sessions from previous process — they're
    # ephemeral by design and must not survive a restart.
    try:
        from core.database import SessionLocal as _SL, Session as _DbSess, ChatMessage as _DbMsg
        _db = _SL()
        try:
            _ghosts = _db.query(_DbSess).filter(_DbSess.name.in_(("Nobody", "Incognito"))).all()
            for _g in _ghosts:
                _db.query(_DbMsg).filter(_DbMsg.session_id == _g.id).delete()
                _db.delete(_g)
            if _ghosts:
                _db.commit()
                logger.info(f"Purged {len(_ghosts)} leftover incognito session(s)")
        finally:
            _db.close()
    except Exception as e:
        logger.debug(f"Incognito purge skipped: {e}")
    # Strong refs to fire-and-forget startup tasks. Without this, Python may
    # GC tasks created with `asyncio.create_task(...)` before they finish.
    _startup_tasks: list[asyncio.Task] = getattr(app.state, "_startup_tasks", [])
    app.state._startup_tasks = _startup_tasks
    if upload_cleanup_func:
        upload_cleanup_task = asyncio.create_task(upload_cleanup_func())
    # Always-on monitor that auto-continues the agent when a background bash
    # job (#!bg) finishes — re-invokes the turn with the job output.
    try:
        from src.bg_monitor import start_bg_monitor
        _startup_tasks.append(start_bg_monitor())
    except Exception as _e:
        logger.warning("Failed to start background-job monitor: %s", _e)
    # MCP servers can be slow or blocked by local tooling. Connect them after
    # the web server is accepting traffic instead of delaying the whole UI.
    async def _startup_mcp_connections():
        try:
            from src.builtin_mcp import register_builtin_servers
            await register_builtin_servers(mcp_manager)
        except BaseException as e:
            logger.warning(f"Built-in MCP registration failed (non-critical): {type(e).__name__}: {e}")
        try:
            await asyncio.wait_for(mcp_manager.connect_all_enabled(), timeout=20)
        except asyncio.TimeoutError:
            logger.warning("User MCP startup timed out (non-critical)")
        except BaseException as e:
            logger.warning(f"MCP startup failed (non-critical): {type(e).__name__}: {e}")

    _startup_tasks.append(asyncio.create_task(_startup_mcp_connections()))

    # Pre-warm the RAG tool index off the request path. Loading the local
    # embedding model + opening ChromaDB + indexing the built-in tools is a
    # one-time ~1-3s cost that otherwise lands on the user's FIRST message
    # (showing up as a big `tool_selection` time). Doing it here makes the
    # first turn as fast as subsequent ones (warm embed ≈ a few ms).
    async def _warmup_tool_index():
        try:
            from src.tool_index import get_tool_index
            idx = await asyncio.to_thread(get_tool_index)
            if idx:
                await asyncio.to_thread(idx.get_tools_for_query, "warmup", 8)
                logger.info("[startup] Tool index pre-warmed")
        except Exception as e:
            logger.warning(f"Tool index warmup failed (non-critical): {type(e).__name__}: {e}")

    _startup_tasks.append(asyncio.create_task(_warmup_tool_index()))
    # Warmup: ping all known LLM endpoints to prime connections
    async def _warmup_endpoints():
        try:
            import httpx
            endpoints = model_discovery.get_endpoints() if model_discovery else []
            for ep in endpoints[:5]:
                url = ep.get("url", "").replace("/chat/completions", "/models")
                if url:
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            await client.get(url)
                        logger.info(f"Warmup ping OK: {url}")
                    except Exception as e:
                        logger.debug(f"Warmup ping failed for endpoint: {e}")
        except Exception as e:
            logger.debug(f"Warmup ping skipped: {e}")

    _startup_tasks.append(asyncio.create_task(_warmup_endpoints()))

    # Keep-alive: ping endpoints every 60 seconds to prevent cold starts
    async def _keepalive_loop():
        while True:
            try:
                await asyncio.sleep(60)
                await _warmup_endpoints()
            except Exception as e:
                logger.warning(f"Keepalive loop error: {e}")
                await asyncio.sleep(300)  # Back off on error

    _startup_tasks.append(asyncio.create_task(_keepalive_loop()))

    async def _ensure_default_tasks():
        # Create/reconcile default automation tasks + personal assistant for every user.
        owners = set()
        try:
            import json as _json
            auth_path = "data/auth.json"
            with open(auth_path, encoding="utf-8") as f:
                users = _json.load(f).get("users", {})
            owners.update(users.keys())
        except Exception as e:
            logger.debug(f"Default task auth-owner scan: {e}")

        # Also reconcile owners already present in scheduled_tasks. This cleans
        # up stale/demo/deleted-user built-ins that are no longer in auth.json;
        # otherwise their old scheduled rows can keep firing forever.
        try:
            from core.database import SessionLocal, ScheduledTask
            from src.task_scheduler import HOUSEKEEPING_DEFAULTS
            builtin_names = []
            for defs in HOUSEKEEPING_DEFAULTS.values():
                builtin_names.append(defs["name"])
                builtin_names.extend(defs.get("legacy_names") or [])
            db_seed = SessionLocal()
            try:
                rows = db_seed.query(ScheduledTask.owner).filter(
                    (ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())))
                    | (ScheduledTask.name.in_(builtin_names))
                ).distinct().all()
                owners.update(row[0] for row in rows if row[0])
            finally:
                db_seed.close()
        except Exception as e:
            logger.debug(f"Default task existing-owner scan: {e}")

        try:
            for uname in sorted(owners):
                try:
                    await task_scheduler.ensure_defaults(uname)
                except Exception as e:
                    logger.debug(f"ensure_defaults({uname}): {e}")
        except Exception as e:
            logger.debug(f"Default tasks: {e}")

    # Reconcile built-in tasks before the runner starts. Otherwise legacy
    # scheduled built-ins can fire once before being converted to event tasks.
    await _ensure_default_tasks()

    # Disk-backed skills are not covered by the DB legacy-owner sweep. Repair
    # ownerless or deleted/test-owner SKILL.md files so strict owner filtering
    # does not make an existing library look empty after auth/account changes.
    try:
        import json as _json
        auth_path = "data/auth.json"
        with open(auth_path, encoding="utf-8") as f:
            users = _json.load(f).get("users", {})
        primary_owner = None
        for uname, udata in users.items():
            if udata.get("is_admin") is True:
                primary_owner = uname
                break
        if not primary_owner and users:
            primary_owner = next(iter(users))
        if primary_owner:
            changed = skills_manager.backfill_owner(primary_owner, set(users.keys()))
            if changed:
                logger.info("Assigned %s legacy skill file(s) to %s", changed, primary_owner)
    except Exception as e:
        logger.debug(f"Skill owner backfill skipped: {e}")

    # Start scheduled task runner — skip when running under a cron-driven
    # deployment where an external worker drives task firing. Mirrors
    # `ODYSSEUS_INPROCESS_POLLERS` from the email pollers.
    _tasks_inprocess = os.environ.get("ODYSSEUS_INPROCESS_TASKS", "1").strip().lower()
    if _tasks_inprocess not in ("0", "false", "no", "off", ""):
        await task_scheduler.start()
    else:
        logger.info(
            "In-process task scheduler disabled (ODYSSEUS_INPROCESS_TASKS=0); "
            "drive task firing externally (e.g. cron)."
        )
    # Periodic null-owner sweep — re-runs the legacy-owner assignment hourly
    # so any data created while auth was disabled / localhost-bypassed gets
    # claimed by the admin instead of staying world-visible (M19).
    async def _null_owner_sweep_loop():
        while True:
            try:
                await asyncio.sleep(3600)
                from core.database import _migrate_assign_legacy_owner
                await asyncio.to_thread(_migrate_assign_legacy_owner)
            except Exception as e:
                logger.debug(f"Null-owner sweep skipped: {e}")
                await asyncio.sleep(3600)

    _startup_tasks.append(asyncio.create_task(_null_owner_sweep_loop()))

    # Nightly skill audit — at ~02:00 local, test + judge a batch of the
    # least-recently-checked skills, auto-fixing/escalating weak ones (never
    # deletes). Rotates through the library so each night covers different
    # skills. Gated by the `skill_audit_nightly` setting (default on); hour via
    # `skill_audit_hour` (default 2), batch size via `skill_audit_batch` (8).
    async def _skill_audit_nightly_loop():
        from datetime import timedelta
        while True:
            try:
                from src.settings import get_setting
                hour = int(get_setting("skill_audit_hour", 2) or 2)
            except Exception:
                hour = 2
            now = datetime.now()
            nxt = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep(max(60, (nxt - now).total_seconds()))
            try:
                from src.settings import get_setting
                if not get_setting("skill_audit_nightly", True):
                    continue
                batch = int(get_setting("skill_audit_batch", 8) or 8)
                from routes.skills_routes import run_scheduled_skill_audit
                await run_scheduled_skill_audit(skills_manager, owner=None, max_skills=batch)
            except Exception as e:
                logger.warning(f"Nightly skill audit failed: {e}")

    _startup_tasks.append(asyncio.create_task(_skill_audit_nightly_loop()))

    # Cookbook serve lifecycle — kills scheduler-launched serves whose
    # window-end has passed. Paired with the cookbook_serve builtin
    # action; both are no-ops unless a scheduled task actually launches
    # something with end_after_min set. Removing this line + the
    # cookbook_serve entry in BUILTIN_ACTIONS + src/cookbook_serve_lifecycle.py
    # removes the feature.
    from src.cookbook_serve_lifecycle import cookbook_serve_lifecycle_loop
    _startup_tasks.append(asyncio.create_task(cookbook_serve_lifecycle_loop()))

    logger.info("Application startup complete")

async def _shutdown_event():
    logger.info("Application shutting down...")
    if upload_cleanup_task:
        upload_cleanup_task.cancel()
        try:
            await upload_cleanup_task
        except asyncio.CancelledError:
            pass
    # Stop task scheduler (no-op if it never started under the gate)
    try:
        await task_scheduler.stop()
    except Exception:
        pass
    # Close webhook manager
    try:
        await webhook_manager.close()
    except Exception as e:
        logger.warning(f"Webhook manager shutdown error: {e}")
    # Disconnect all MCP servers
    try:
        await mcp_manager.disconnect_all()
    except Exception as e:
        logger.warning(f"MCP shutdown error: {e}")
    logger.info("Application shutdown complete")
