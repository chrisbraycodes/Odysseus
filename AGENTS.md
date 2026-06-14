# Agent instructions (Odysseus)

Read this before changing workspace IDE layout, mobile panels, or related CSS/JS.

## Non‑negotiable: never hide the core IDE panels (desktop)

On **desktop** (viewport width **> 768px**, `body.ide-layout-desktop`), these three regions must **always remain visible** in the workspace IDE:

| Region | DOM / module |
|--------|----------------|
| **Project file tree** | `#ws-explorer-pane` · `workspaceExplorer.js` |
| **Editor** | `#doc-editor-pane` inside `#ws-workbench-column` · `document.js` |
| **Terminal** | `#ws-terminal-dock` (desktop) · `workspaceTerminal.js` |

Plus chat (`#chat-container`) stays visible as the right column.

**Do not:**

- Set `display: none`, `visibility: hidden`, `height: 0`, `opacity: 0`, or inline `display: none !important` on any of the above on desktop.
- Leave `body.ws-mob-view` or mobile inline panel styles active after the viewport is desktop-wide.
- Hide `#ws-workbench-column` or `#ws-terminal-dock` to “fix” mobile — those selectors are **mobile-only** (`body.ws-mob-view`, max-width 768px).
- Use empty-state overlays that hide the real editor/tree/terminal panel on desktop.
- Ship a mobile layout change without testing desktop: **files | editor + terminal | chat** must still show.

**When leaving mobile layout**, always tear down mobile state and restore desktop IDE (`unmountMobilePanels()` → `restoreWorkspaceIde()`). See `docs/workspace-ide-layout.md`.

## Mobile (≤ 768px)

Mobile uses a bottom tab bar and at most **two** panels side by side (`wsMobilePanels.js`). That is the **only** context where individual panels may be off-screen — and only while `body.ws-mob-view` is active **and** `isMobileIdeLayout()` is true.

Mobile rules must **never** leak to desktop. Guard all mobile-only hide/show logic with `isMobileIdeLayout()`.

## Key files

| File | Role |
|------|------|
| `static/js/ideLayoutMode.js` | Breakpoint 768px, `ide-layout-desktop` / `ide-layout-mobile` body classes |
| `static/js/wsMobilePanels.js` | Mobile tab bar; must clean up on desktop |
| `static/js/workspaceExplorer.js` | File tree + workbench column + `restoreWorkspaceIde()` |
| `static/js/document.js` | Editor pane + workspace file tabs |
| `static/ws-mob.css` | Mobile-only styles (`body.ws-mob-view`) |
| `static/style.css` | Desktop IDE under `body.ws-explorer-view` (min-width 769px rules) |

Full layout contract: **[docs/workspace-ide-layout.md](docs/workspace-ide-layout.md)**

## Static assets in Docker

JS/CSS under `static/` are baked into the image, not volume-mounted. After layout changes: `docker compose up -d --build odysseus` and hard-refresh the browser.
