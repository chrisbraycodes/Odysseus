# Workspace IDE layout contract

This document defines how the Prometheus Source workspace IDE must behave. **Agents and contributors: read [AGENTS.md](../AGENTS.md) first.**

---

## Desktop layout (viewport > 768px)

The desktop IDE is a **three-column** layout. All three core columns must **always be visible** when `body.ws-explorer-view` is active:

```
┌─────────────┬──────────────────────────┬─────────────┐
│ Project     │ Editor                   │ Chat        │
│ files       │ (doc-editor-pane)        │             │
│             ├──────────────────────────┤             │
│             │ Terminal (ws-terminal-  │             │
│             │ dock)                    │             │
└─────────────┴──────────────────────────┴─────────────┘
```

### Hard rules (desktop)

1. **Never hide** `#ws-explorer-pane`, `#doc-editor-pane`, `#ws-workbench-column`, or `#ws-terminal-dock` on desktop.
2. **Never** apply `body.ws-mob-view` styles on desktop. That class hides the workbench column and terminal dock in CSS.
3. **Never** leave inline `style="display: none !important"` (or similar) on IDE panels after a viewport or layout mode change.
4. **Always** call `restoreWorkspaceIde()` after unmounting mobile panels when returning to desktop.
5. **Always** gate mobile-only JavaScript with `isMobileIdeLayout()` from `ideLayoutMode.js`.
6. Empty-state placeholders (`ws-mob-panel-empty`) are **mobile-only**; they must not replace or hide real panels on desktop.

### Expected body classes (desktop IDE open)

- `ws-explorer-view` — file tree column mounted
- `doc-view` — editor pane open (via `document.js` / `restoreWorkspaceIde()`)
- `ide-layout-desktop` — viewport-driven; not mobile tab layout

### Restore path

On load and after layout transitions, `ensureIdeLayoutOpen()` → `restoreWorkspaceIde()` should:

1. Open explorer (`ws-explorer-view`)
2. Ensure `#ws-workbench-column` exists
3. Mount editor in workbench (`_ensureEditorInWorkbench`, `openPanel`)
4. Mount terminal in `#ws-terminal-dock`

---

## Mobile layout (viewport ≤ 768px)

Mobile uses `body.ws-mob-view` and `wsMobilePanels.js`:

- Bottom tab bar: Chat · Files · Editor · Term
- At most **two** panels visible side by side
- Panels not in the current selection may be hidden — **only in this mode**

### Mobile rules

1. All hide/show logic in `wsMobilePanels.js` must start with `if (!isMobileIdeLayout()) return` (or equivalent).
2. `_cleanup()` / `unmountMobilePanels()` must remove `ws-mob-view`, clear inline panel styles, and remove the tab bar **every time** desktop layout applies — not only when `_active === true`.
3. After cleanup on desktop, if mobile artifacts existed, call `restoreWorkspaceIde()`.
4. Do not dispatch global `window.resize` from mobile panel code on desktop (causes flicker/regressions).

---

## CSS map

| Selector | Scope | Notes |
|----------|--------|--------|
| `body.ws-explorer-view .ws-explorer-pane` | Desktop IDE | File tree column |
| `body.ws-explorer-view .ws-workbench-column` | Desktop IDE | Editor + terminal stack |
| `body.ws-explorer-view .ws-workbench-column .doc-editor-pane` | Desktop IDE | Editor fills top of workbench |
| `body.ws-explorer-view .ws-terminal-dock` | Desktop IDE | Terminal dock |
| `body.ws-mob-view #ws-workbench-column` | **Mobile only** | `display: none !important` — must not apply on desktop |
| `body.ws-mob-view #ws-terminal-dock` | **Mobile only** | Hidden; use `#ws-mob-terminal-panel` instead |

---

## Common regression patterns (do not repeat)

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Mobile `_apply()` sets inline `display: none !important` on panels; cleanup skipped on desktop | File tree / editor / terminal missing on wide viewport | Always run `_clearMobilePanelStyles()`; call `restoreWorkspaceIde()` |
| `body.ws-mob-view` stuck after resize | Workbench + terminal CSS-hidden | Remove class in `_cleanup()`; sync on `IDE_LAYOUT_EVENT` |
| Empty-state logic runs on desktop | Editor hidden when no file tab | Guard with `isMobileIdeLayout()` |
| Editor pane left as direct `body` child after mobile DOM reorder | Editor not in workbench flex layout | `_adoptEditorIntoWorkbench()` in `restoreWorkspaceIde()` |

---

## Testing checklist

After any change to `wsMobilePanels.js`, `ws-mob.css`, `workspaceExplorer.js`, `ideLayoutMode.js`, or desktop IDE CSS:

1. **Desktop (>768px):** Open `/workspace` — file tree, editor (tab bar visible), terminal, and chat all visible.
2. **Resize to mobile:** Tab bar appears; two panels max.
3. **Resize back to desktop:** All three IDE columns visible again; no stuck `ws-mob-view`.
4. Hard refresh after Docker rebuild (`docker compose up -d --build odysseus`).

---

## Related modules

- `static/js/workspaceExplorer.js` — explorer pane, workbench column, terminal dock, restore
- `static/js/wsMobilePanels.js` — mobile tab layout
- `static/js/ideLayoutMode.js` — 768px breakpoint
- `static/js/document.js` — editor pane and workspace file tabs
- `static/js/wsPanelResize.js` — desktop column resize handles
