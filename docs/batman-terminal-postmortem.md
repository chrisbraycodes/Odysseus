# Postmortem: `npx create-react-app batman` (Jun 7, 2026)

This documents **every reason** the batman React scaffold did not land where you expected (`/workspace/batman` on your Desktop), what the browser terminal did vs. did not do, and the **root-cause fixes** applied (not shortcuts).

---

## What you expected

1. Workspace = `/workspace` (Desktop mount in Docker).
2. Agent runs `npx create-react-app batman` in that folder.
3. Plan step 1 marked done; step 2 `cd batman` becomes runnable.
4. Browser terminal might help run or show the command.

## What actually happened

| Observation | Evidence |
|-------------|----------|
| No `batman/` on Desktop | `/workspace/batman` missing in container |
| Agent claimed step 1 done | Log: `0 native calls, 0 tool blocks` — **no bash executed** |
| `batman/` exists elsewhere | `/app/data/batman/` from an earlier run (~17:57 UTC) |
| Browser terminal connected | WebSocket `[accepted]` to `/workspace` — but **no agent traffic** |
| `update_plan` failed | `python: update_plan(...)` → `exit_code=1` (wrong fence + unverified tick) |

---

## Root causes (complete list)

### 1. Browser terminal ≠ agent shell

**Reason:** The IDE xterm panel is a **manual PTY for the user**. The agent never writes to it. Agent shell work goes through the **`bash` tool** (subprocess in the server), a separate code path.

**What the terminal did:** Opened WebSocket PTY, showed a prompt in `/workspace` or `/workspace/Odysseus/odysseus`.

**What it did not do:** Run `npx create-react-app`, receive agent commands, or mirror chat tool output.

**Fix:** Terminal dock label **“Manual shell”** with tooltip explaining agent uses chat bash tools. Documented here; no fake “agent terminal” bridge (that would be a different feature).

---

### 2. xterm.js failed to load initially (CDN)

**Reason:** Terminal UI loaded xterm via `cdn.jsdelivr.net` dynamic `import()`. That CDN was **unreachable** from your network → stuck on “Loading xterm.js…”.

**Fix (already shipped):** Bundle xterm 5.5 under `static/lib/xterm/`; serve locally; Docker volume mount for live dev.

---

### 3. Docker image missing terminal backend

**Reason:** Container was built before terminal routes existed. `/api/terminal/ws` returned **404**; `websockets` package missing → PTY could not connect even after xterm loaded.

**Fix (already shipped):** Mount `terminal_routes.py`, `terminal_manager.py`, `app.py`; add `websockets` to `requirements.txt`; compose volume for xterm lib.

---

### 4. Local model: no native tool schemas (`tools_sent=0`)

**Reason:** Qwen2.5-Coder on a local endpoint is treated as **non-API** (`_is_api_model=False`). Odysseus does **not** send OpenAI function schemas; the model must emit fenced blocks:

```bash
npx create-react-app batman
```

The model wrote prose (“Ran `npx create-react-app batman`”) instead → **0 tool blocks parsed**.

**Fix:**  
- **Plan auto-shell:** Before LLM, extract the next unchecked `- [ ]` step with a shell command from the approved plan and run it via `bash` automatically.  
- **False-completion recovery:** If the model claims it “Ran/executed/scaffolded …” with zero tool blocks, detect the command and auto-run it, then inject results into the next round.

---

### 5. Plan approval did not trigger auto-shell for `npx`

**Reason:** Auto-shell only ran for **literal one-line user messages** (e.g. `npm start`) or empty message + `npm start` in plan. Your message was **“Approved — execute the plan”**, and step 1 was `npx create-react-app batman` — not matched.

**Fix:** `detect_auto_shell_command()` now calls `extract_next_plan_shell_command(approved_plan)` to run the first unchecked shell step (including `` `npx create-react-app batman` `` in backticks).

---

### 6. Model hallucinated completion (past tense, no tools)

**Reason:** Intent nudge only caught future tense (“Let me run…”), not **past tense lies** (“Ran `npx…`”). Round ended with no tools and a checked plan in prose only.

**Fix:** `detect_false_completion_command()` in `src/plan_execution.py` + agent-loop recovery (see #4).

---

### 7. `update_plan` misfenced as `python`

**Reason:** Model emitted:

```python
update_plan("1. npx create-react-app batman - [x]\n2. cd batman - [ ]")
```

Parser treated it as **Python code** → invalid execution → `exit_code=1`.

**Fix:** `_parse_misfenced_update_plan()` in `tool_parsing.py` (same pattern as misfenced `web_search()`).

---

### 8. `update_plan` allowed false checkmarks without disk proof

**Reason:** `update_plan` accepted any markdown; no check that `batman/package.json` existed before marking create-react-app done.

**Fix:** `verify_plan_checkmarks()` rejects `- [x]` on create-react-app / `cd` steps when the target folder is missing under the active workspace.

---

### 9. Scaffold landed in `/app/data/` instead of `/workspace/`

**Reason:** When workspace was unset or cwd resolved to Odysseus `data/`, `npx create-react-app` created **`data/batman`** (persisted volume), not Desktop. `HOME` for subprocesses is also `data/` (npm cache), which confused debugging.

**Fix:** `resolve_scaffold_cwd()` redirects bash cwd from `data/` to `/workspace` when the Desktop mount exists.

---

### 10. Workspace pill vs. agent cwd mismatch (UX)

**Reason:** User had workspace `/workspace/Odysseus/odysseus` at times, then `/workspace`. Logs show validation for both. Scaffold must run in the **active workspace pill**, not wherever an old `batman` folder already exists under `data/`.

**Fix:** Workspace system note already pins cwd; auto-shell and verification use the same `workspace` argument passed to `execute_tool_block`. Doc + verification prevent silent wrong-folder success.

---

### 11. `create-react-app` auto-backgrounded

**Reason:** Long `npx` commands get `#!bg` marker → background job. Agent may declare success before the job finishes if the model hallucinates (not if auto-shell runs — bg followup monitor re-invokes agent with output).

**Fix:** False-completion and plan auto-shell integrate with existing bg job monitor; `update_plan` verification prevents ticking scaffold done until `package.json` exists.

---

### 12. Weak model + plan mode tooling gap

**Reason:** During plan **execution**, `tools_sent=0` still applies. Model relied on prose + broken `update_plan` instead of bash.

**Fix:** Combined: plan auto-shell (proactive), false-completion recovery (reactive), misfenced `update_plan`, verification gate.

---

### 13. Skill markdown 404 (minor)

**Reason:** UI requested `/api/skills/create-react-app/markdown` → 404. Did not block scaffold but added noise.

**Status:** Informational; skill exists under `data/skills/general/create-react-app/SKILL.md`. Route mismatch is separate from terminal failure.

---

## Log timeline (Docker, Jun 7 ~22:15 UTC)

```
[workspace] active for this turn: /workspace
tools_sent=0  model=Qwen2.5-Coder-7B-Instruct
Agent round 1: 0 native calls, 0 tool blocks — "Ran npx create-react-app batman…"
WebSocket /api/terminal/ws?workspace=/workspace  [accepted]
Tool executed: python: update_plan(...) -> exit_code=1
```

No `Tool executed: bash` lines in this session.

---

## Files changed for fixes

| Area | File |
|------|------|
| Plan parse / verify / false completion | `src/plan_execution.py` (new) |
| Auto-run next plan shell step | `src/shell_orchestration.py` |
| Misfenced `update_plan` | `src/tool_parsing.py` |
| Verify ticks + scaffold cwd | `src/tool_execution.py` |
| False-completion + plan auto-shell loop | `src/agent_loop.py` |
| Terminal UX label | `static/js/workspaceExplorer.js`, `static/style.css` |
| xterm local bundle | `static/lib/xterm/`, `static/js/workspaceTerminal.js` |
| Docker / deps | `docker-compose.yml`, `requirements.txt` |

---

## How to verify fixes

1. Set workspace pill to `/workspace`.
2. Approve plan with `- [ ] Run npx create-react-app batman`.
3. Send “execute the plan” — expect **auto bash tool** before or instead of model prose.
4. Confirm `/workspace/batman/package.json` exists on Desktop.
5. `update_plan` with `- [x]` on step 1 **succeeds** only after folder exists.
6. Browser terminal: prompt works; label says **Manual shell**.

---

## Related tests

- `tests/test_plan_execution.py`
- `tests/test_shell_orchestration.py` (extended)
- `tests/test_update_plan_tool.py` (extended)
