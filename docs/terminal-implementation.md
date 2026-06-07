# Workspace Terminal — Implementation Plan (2026)

> **Goal:** Replace the workspace explorer “Terminal coming soon” placeholder with a full-fidelity interactive terminal (persistent shell, ANSI colors, resize, Ctrl+C, workspace `cwd`, scrollback).

**Architecture:** xterm.js (browser) ↔ binary WebSocket ↔ Python PTY host (`pywinpty` on Windows, `pty` on POSIX). No Electron/Tauri required for v1 — the existing FastAPI server hosts the PTY.

**References (2026):**
- [xterm.js](https://github.com/xtermjs/xterm.js) + `@xterm/addon-attach`, `@xterm/addon-fit`
- [pywinpty v3](https://pypi.org/project/pywinpty/) — ConPTY on Windows
- [term-wrapper](https://github.com/rom1504/term-wrapper) — FastAPI + WebSocket PTY pattern
- [AutoForge terminal_manager](https://github.com/AutoForgeAI/autoforge) — cross-platform PTY abstraction

---

## Progress tracker

| Phase | Scope | Status | Tests | Commit |
|-------|--------|--------|-------|--------|
| 0 | This plan doc | ✅ Done | — | — |
| 1 | Backend: `terminal_manager.py`, `/api/terminal/ws`, admin auth, workspace cwd | ✅ Done | 10/10 pass | pushed |
| 2 | Frontend: xterm.js in workspace panel, loading/status UI | ✅ Done | 10/10 pass | pushed |
| 3 | Polish: resize sync, scrollback buffer, theme, web-links | ✅ Done | 10/10 pass | pushed |
| 4 | Multi-tab terminals, reconnect | ✅ Done | 10/10 pass | pushed |
| 5 | Optional: Tauri desktop shell (loads localhost, same WS) | ⏳ Deferred | — | — |

**Overall completion:** 100% (Phases 1–4 — embedded terminal feature-complete)

---

## Phase 1 — Backend PTY + WebSocket

### Deliverables
- `src/terminal_manager.py` — cross-platform PTY session (`TerminalSession`)
- `routes/terminal_routes.py` — `WS /api/terminal/ws?workspace=…`
- Register router in `app.py`
- `pywinpty` in `requirements-optional.txt` (Windows ConPTY)
- Admin-only auth (matches workspace + shell routes)
- `cwd` = validated workspace folder

### Protocol
- **Binary frames:** raw terminal I/O (stdin/stdout bytes)
- **Text JSON frames:** `{"type":"resize","cols":80,"rows":24}`

### Test criteria
- Non-admin WebSocket → close 4403
- Admin WebSocket with valid workspace → shell starts
- `echo hello` output received
- Disconnect kills PTY process

---

## Phase 2 — Frontend xterm.js

### Deliverables
- `static/js/workspaceTerminal.js` — xterm + FitAddon + AttachAddon
- Replace placeholder in `workspaceExplorer.js`
- Loading states: `Connecting…`, `Starting shell…`, `Connected`, error with retry
- CSP: allow xterm CDN (already jsdelivr in middleware)

### Test criteria
- Open project files panel → terminal shows status then shell prompt
- Type `dir` / `ls` → output appears
- Panel close disconnects WebSocket

---

## Phase 3 — Polish (~98% fidelity)

### Deliverables
- ResizeObserver → `resize` JSON to server
- Server-side output ring buffer (~512 KB) for reconnect
- xterm theme from Odysseus CSS variables
- `@xterm/addon-web-links` for clickable URLs/paths

### Test criteria
- Window resize updates PTY dimensions
- Reconnect replays recent scrollback
- Ctrl+C interrupts running command

---

## Phase 4 — Multi-tab (~100% for embedded use)

### Deliverables
- Tab bar in terminal section (+ / close)
- Session ID per tab (`/api/terminal/ws?workspace=…&session=…`)
- `TerminalSessionManager` keyed by session id

### Test criteria
- Two tabs → independent shells, independent cwd state
- Close tab kills only that PTY

---

## Phase 5 — Optional desktop wrapper (future)

Thin Tauri 2 shell loading `http://127.0.0.1:7000` — terminal unchanged (same WebSocket). Native IPC (`tauri-plugin-pty`) only if offline-from-server is needed.

---

## Install notes (Windows)

```powershell
pip install pywinpty
# or
pip install -r requirements-optional.txt
```

Without `pywinpty`, the terminal shows a clear install hint on Windows.

---

## Changelog

### 2026-06-07 — Phase 0
- Created implementation plan
- Starting Phase 1 backend work

### 2026-06-07 — Phase 1 ✅
- Added `src/terminal_manager.py` (pywinpty ConPTY on Windows, pty on POSIX)
- Added `routes/terminal_routes.py` — `WS /api/terminal/ws`
- Registered router in `app.py`
- Added `pywinpty` to `requirements-optional.txt` (Windows marker)
- Tests: `tests/test_terminal_routes.py` — **10/10 passed** on Windows + pywinpty 3.x

### 2026-06-07 — Phase 2 ✅
- Added `static/js/workspaceTerminal.js` — xterm.js 5.5 + FitAddon + WebLinksAddon
- Replaced workspace explorer placeholder with live terminal mount
- Status bar phases: Loading xterm.js → Connecting → Starting shell → Connected
- Reconnect button on error/disconnect
- CSP: `connect-src` allows cdn.jsdelivr.net for xterm ESM imports

### 2026-06-07 — Phase 3 ✅
- ResizeObserver + `resize` JSON sync to PTY
- Server scrollback ring buffer (512 KB) replayed on reconnect
- xterm theme from Odysseus CSS variables (`--bg`, `--fg`, `--brand-color`)
- Web links addon for clickable URLs

### 2026-06-07 — Phase 4 ✅
- Multi-tab terminal panel (`createWorkspaceTerminalPanel`) with + / × controls
- Independent WebSocket session per tab (`session` query param)
- Workspace change triggers `reconnectAll()` in all tabs
