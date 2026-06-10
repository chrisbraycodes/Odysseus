// static/js/wsMobilePanels.js  —  Mobile 1-or-2-panel layout
//
// At most 2 panels visible at once, side by side with a drag handle.
// Each tab button independently opens/closes its own panel.
// The sidebar (Odysseus menu) is untouched — it keeps its own collapse control.

const BREAKPOINT  = 768;
const LS_KEY      = 'ws-mob-v3';
const MIN_PCT     = 20;   // minimum panel width as % of available space
const TAB_H       = 56;   // tab bar height in px

// ── Panel definitions ─────────────────────────────────────────────────────────

const TABS = [
  {
    id: 'chat', label: 'Chat',
    getEl: () => document.getElementById('chat-container'),
    onOpen: null,
    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  },
  {
    id: 'files', label: 'Files',
    getEl: () => document.getElementById('ws-explorer-pane'),
    onOpen: () => { try { document.dispatchEvent(new CustomEvent('open-workspace-explorer')); } catch (_) {} },
    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  },
  {
    id: 'editor', label: 'Editor',
    getEl: () => document.getElementById('ws-workbench-column') || document.getElementById('doc-editor-pane'),
    onOpen: () => { try { window.documentModule?.openPanel?.(); } catch (_) {} },
    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`,
  },
  {
    id: 'terminal', label: 'Term',
    getEl: () => document.getElementById('ws-mob-terminal-panel'),
    onOpen: () => { try { document.dispatchEvent(new CustomEvent('open-workspace-explorer')); } catch (_) {} },
    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  },
];

// ── State ──────────────────────────────────────────────────────────────────────

// Ordered array of open panel IDs, left to right. Max 2.
let _panels   = ['chat'];
// Left panel's % of available width when 2 panels are open
let _splitPct = 50;

let _bar    = null;
let _handle = null;   // the single split handle (shown when 2 panels open)
let _drag   = null;

// ── Persistence ───────────────────────────────────────────────────────────────

function _save() {
  try { localStorage.setItem(LS_KEY, JSON.stringify({ panels: _panels, splitPct: _splitPct })); } catch (_) {}
}

function _load() {
  try {
    const d = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
    if (!d) return;
    if (Array.isArray(d.panels) && d.panels.length) _panels = d.panels.slice(0, 2);
    if (typeof d.splitPct === 'number') _splitPct = d.splitPct;
    if (!_panels.length) _panels = ['chat'];
  } catch (_) {}
}

// ── Geometry ──────────────────────────────────────────────────────────────────

function _sidebarW() { return document.getElementById('sidebar')?.offsetWidth || 0; }
function _vpH()      { return window.visualViewport?.height ?? window.innerHeight; }
function _kbH()      { return Math.max(0, window.innerHeight - _vpH()); }

// ── Layout apply ──────────────────────────────────────────────────────────────

function _apply() {
  const sw     = _sidebarW();
  const kbH    = _kbH();
  const vpH    = _vpH();
  const availW = window.innerWidth - sw;
  const availH = vpH - TAB_H;

  // Compute each visible panel's pixel width
  const widths = {};
  if (_panels.length === 1) {
    widths[_panels[0]] = availW;
  } else {
    const leftW         = Math.round(availW * _splitPct / 100);
    widths[_panels[0]]  = leftW;
    widths[_panels[1]]  = availW - leftW;
  }

  // Apply styles to each panel
  TABS.forEach(({ id, getEl }) => {
    const el = getEl();
    if (!el) return;

    if (_panels.includes(id)) {
      const w = widths[id];
      // setProperty with 'important' beats any stylesheet !important
      el.style.setProperty('display',    'flex',    'important');
      el.style.setProperty('width',      `${w}px`,  'important');
      el.style.setProperty('max-width',  `${w}px`,  'important');
      el.style.setProperty('min-width',  '0',       'important');
      el.style.setProperty('flex',       `0 0 ${w}px`, 'important');
      el.style.setProperty('height',     `${availH}px`, 'important');
      el.style.setProperty('max-height', `${availH}px`, 'important');
      el.style.setProperty('overflow',   'hidden',  'important');
      el.style.setProperty('position',   'relative','important');
    } else {
      el.style.setProperty('display', 'none', 'important');
    }
  });

  // Tab bar above keyboard
  if (_bar) _bar.style.bottom = `${kbH}px`;

  // Body height matches visible area (so nothing hides behind keyboard)
  document.body.style.setProperty('height', `${availH}px`, 'important');

  // Split handle
  if (_panels.length === 2) {
    const x = sw + Math.round(availW * _splitPct / 100);
    _handle.style.display  = 'flex';
    _handle.style.left     = `${x}px`;
    _handle.style.top      = '0';
    _handle.style.bottom   = `${TAB_H + kbH}px`;
    _handle.style.transform = 'translateX(-50%)';
  } else {
    _handle.style.display = 'none';
  }

  // Tab button active states
  _bar?.querySelectorAll('.ws-mob-tab').forEach(btn => {
    const on = _panels.includes(btn.dataset.panel);
    btn.classList.toggle('ws-mob-tab-active', on);
    btn.setAttribute('aria-pressed', String(on));
  });

  _save();

  // Let editors (xterm, CodeMirror) re-measure
  requestAnimationFrame(() => { try { window.dispatchEvent(new Event('resize')); } catch(_){} });
}

// ── Toggle ────────────────────────────────────────────────────────────────────

function _toggle(id) {
  const tab = TABS.find(t => t.id === id);
  if (!tab) return;

  const idx = _panels.indexOf(id);

  if (idx >= 0) {
    // Panel is open — close it (keep at least 1)
    if (_panels.length <= 1) return;
    _panels.splice(idx, 1);
  } else {
    // Panel is closed — open it
    if (!tab.getEl() && tab.onOpen) tab.onOpen();
    if (_panels.length >= 2) {
      // Replace the right-side (most recently added) panel
      _panels.pop();
    }
    _panels.push(id);
  }

  _apply();
}

// ── Split handle ──────────────────────────────────────────────────────────────

function _makeHandle() {
  const h = document.createElement('div');
  h.className = 'ws-mob-handle ws-mob-handle-v';
  h.setAttribute('role', 'separator');
  h.setAttribute('aria-label', 'Drag to resize panels');
  h.innerHTML = '<div class="ws-mob-handle-bar"></div>';
  h.style.display = 'none';

  const start = (e) => {
    const sw    = _sidebarW();
    const availW = window.innerWidth - sw;
    _drag = {
      startX:    (e.touches?.[0] ?? e).clientX,
      startPct:  _splitPct,
      availW,
    };
    h.classList.add('dragging');
    if (e.cancelable) e.preventDefault();
  };

  h.addEventListener('pointerdown', start, { passive: false });
  h.addEventListener('touchstart',  start, { passive: false });

  document.addEventListener('pointermove', (e) => {
    if (!_drag) return;
    if (e.cancelable) e.preventDefault();
    const dx  = (e.touches?.[0] ?? e).clientX - _drag.startX;
    const dPct = (dx / _drag.availW) * 100;
    _splitPct  = Math.max(MIN_PCT, Math.min(100 - MIN_PCT, _drag.startPct + dPct));
    _apply();
  }, { passive: false });

  document.addEventListener('touchmove', (e) => {
    if (!_drag) return;
    if (e.cancelable) e.preventDefault();
    const dx  = (e.touches?.[0] ?? e).clientX - _drag.startX;
    const dPct = (dx / _drag.availW) * 100;
    _splitPct  = Math.max(MIN_PCT, Math.min(100 - MIN_PCT, _drag.startPct + dPct));
    _apply();
  }, { passive: false });

  document.addEventListener('pointerup',  () => { if (_drag) { h.classList.remove('dragging'); _drag = null; } });
  document.addEventListener('touchend',   () => { if (_drag) { h.classList.remove('dragging'); _drag = null; } });

  return h;
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function _buildBar() {
  const bar = document.createElement('nav');
  bar.id        = 'ws-mob-tabbar';
  bar.className = 'ws-mob-tabbar';
  bar.setAttribute('role', 'tablist');
  bar.setAttribute('aria-label', 'Panel selector');

  TABS.forEach(({ id, label, icon }) => {
    const btn = document.createElement('button');
    btn.type          = 'button';
    btn.className     = 'ws-mob-tab';
    btn.dataset.panel = id;
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = `<span class="ws-mob-icon" aria-hidden="true">${icon}</span><span class="ws-mob-label">${label}</span>`;
    btn.addEventListener('click', () => _toggle(id));
    bar.appendChild(btn);
  });

  return bar;
}

// ── Sidebar resize watcher ────────────────────────────────────────────────────
// Recalculate layout if the sidebar collapses/expands

function _watchSidebar() {
  const sb = document.getElementById('sidebar');
  if (!sb || !window.ResizeObserver) return;
  new ResizeObserver(() => setTimeout(_apply, 30)).observe(sb);
}

// ── Late-arriving panel watcher ───────────────────────────────────────────────

function _watchBody() {
  const KNOWN = new Set(['ws-explorer-pane', 'ws-workbench-column', 'doc-editor-pane', 'ws-mob-terminal-panel']);
  new MutationObserver(muts => {
    for (const m of muts)
      for (const n of m.addedNodes)
        if (n.nodeType === 1 && KNOWN.has(n.id)) { setTimeout(_apply, 30); return; }
  }).observe(document.body, { childList: true });
}

// ── Desktop cleanup ───────────────────────────────────────────────────────────

function _cleanup() {
  document.body.classList.remove('ws-mob-view');
  document.body.style.removeProperty('height');
  TABS.forEach(({ getEl }) => {
    const el = getEl();
    if (!el) return;
    ['display','width','max-width','min-width','flex','height','max-height','overflow','position']
      .forEach(p => el.style.removeProperty(p));
  });
  _handle?.remove();
  _bar?.remove();
}

// ── Init ──────────────────────────────────────────────────────────────────────

export function initMobilePanels() {
  if (!window.matchMedia(`(max-width: ${BREAKPOINT}px)`).matches) return;

  _load();
  document.body.classList.add('ws-mob-view');

  _handle = _makeHandle();
  document.body.appendChild(_handle);

  _bar = _buildBar();
  document.body.appendChild(_bar);

  _apply();
  _watchSidebar();
  _watchBody();

  document.addEventListener('workspace-changed',           () => setTimeout(_apply, 50));
  document.addEventListener('workspace-environment-ready', () => setTimeout(_apply, 50));

  window.addEventListener('resize', () => {
    if (window.matchMedia(`(max-width: ${BREAKPOINT}px)`).matches) setTimeout(_apply, 50);
    else _cleanup();
  });

  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => _apply());
    window.visualViewport.addEventListener('scroll', () => _apply());
  }
}

export default { initMobilePanels };

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMobilePanels);
} else {
  initMobilePanels();
}
