// static/js/wsMobilePanels.js
// Mobile panel system — complete rewrite.
//
// Behaviour:
//   • Each tab button is a pure toggle — tap to show, tap again to hide.
//   • N open panels → auto equal split (100/N % each).
//   • N−1 draggable handles, one between every adjacent pair.
//   • Minimum panel width: 15 %. Widths persist in localStorage.
//   • At least 1 panel always stays open.

const BREAKPOINT = 768;
const LS_ACTIVE  = 'ws-mob-active';
const LS_WIDTHS  = 'ws-mob-widths';
const MIN_W      = 15; // minimum panel width %

const TABS = [
  {
    id: 'chat',
    label: 'Chat',
    getEl: () => document.getElementById('chat-container'),
    onOpen: null,
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  },
  {
    id: 'files',
    label: 'Files',
    getEl: () => document.getElementById('ws-explorer-pane'),
    onOpen: () => { try { document.dispatchEvent(new CustomEvent('open-workspace-explorer')); } catch (_) {} },
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  },
  {
    id: 'editor',
    label: 'Editor',
    getEl: () => document.getElementById('ws-workbench-column') || document.getElementById('doc-editor-pane'),
    onOpen: () => { try { window.documentModule?.openPanel?.(); } catch (_) {} },
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`,
  },
  {
    id: 'terminal',
    label: 'Terminal',
    getEl: () => document.getElementById('ws-mob-terminal-panel'),
    onOpen: () => { try { document.dispatchEvent(new CustomEvent('open-workspace-explorer')); } catch (_) {} },
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  },
];

let _active  = ['chat']; // ordered list of visible panel IDs
let _widths  = [100];    // % width per active panel (same length as _active)
let _handles = [];       // handle DOM elements (length = _active.length - 1)
let _bar     = null;
let _styleEl = null;     // <style> tag for injecting !important width rules
let _drag    = null;     // { handleIdx, startX, startWidths[] }

// ── Persistence ───────────────────────────────────────────────────────────────

function _load() {
  try {
    const a = JSON.parse(localStorage.getItem(LS_ACTIVE) || '["chat"]');
    const w = JSON.parse(localStorage.getItem(LS_WIDTHS)  || 'null');
    _active = a.filter(id => TABS.some(t => t.id === id));
    if (!_active.length) _active = ['chat'];
    if (Array.isArray(w) && w.length === _active.length && w.every(x => typeof x === 'number')) {
      _widths = w;
    } else {
      _equalWidths();
    }
  } catch (_) { _active = ['chat']; _widths = [100]; }
}

function _save() {
  try {
    localStorage.setItem(LS_ACTIVE, JSON.stringify(_active));
    localStorage.setItem(LS_WIDTHS, JSON.stringify(_widths));
  } catch (_) {}
}

// ── Width helpers ─────────────────────────────────────────────────────────────

function _equalWidths() {
  const n = _active.length;
  const w = 100 / n;
  _widths = Array.from({ length: n }, (_, i) => {
    // Last panel absorbs any floating-point remainder
    return i === n - 1 ? +(100 - w * (n - 1)).toFixed(4) : +w.toFixed(4);
  });
}

// Write widths into a dedicated <style> tag using ID selectors + !important
// so they beat any existing flex rules in style.css.
function _flushWidths() {
  if (!_styleEl) return;
  let css = '';
  TABS.forEach(({ id, getEl }) => {
    const el = getEl();
    if (!el) return;
    const slot = _active.indexOf(id);
    if (slot === -1) return;
    const w = _widths[slot];
    // Use both the element ID and the data-mob-slot attribute for max specificity
    css += `#${el.id}[data-mob-slot]{flex:0 0 ${w}% !important;max-width:${w}% !important;min-width:0 !important;}\n`;
  });
  _styleEl.textContent = css;
}

// ── Layout apply ──────────────────────────────────────────────────────────────

function _apply() {
  // 1. Panel visibility + slot order
  TABS.forEach(({ id, getEl }) => {
    const el = getEl();
    if (!el) return;
    const slot = _active.indexOf(id);
    if (slot === -1) {
      el.setAttribute('data-mob-hidden', '1');
      el.removeAttribute('data-mob-slot');
    } else {
      el.removeAttribute('data-mob-hidden');
      el.setAttribute('data-mob-slot', String(slot));
    }
  });

  // 2. Push widths into the style sheet
  _flushWidths();

  // 3. Sync handles (create / remove as needed)
  _syncHandles();

  // 4. Tab button active state
  _bar?.querySelectorAll('.ws-mob-tab').forEach(btn => {
    const on = _active.includes(btn.dataset.panel);
    btn.classList.toggle('ws-mob-tab-active', on);
    btn.setAttribute('aria-pressed', String(on));
  });

  _save();
}

// ── Handles ───────────────────────────────────────────────────────────────────

function _syncHandles() {
  const needed = Math.max(0, _active.length - 1);

  // Remove surplus handles
  while (_handles.length > needed) {
    _handles.pop().remove();
  }

  // Create missing handles
  while (_handles.length < needed) {
    const h = _makeHandle(_handles.length);
    document.body.appendChild(h);
    _handles.push(h);
  }

  // Update indices and show all
  _handles.forEach((h, i) => {
    h.dataset.handleIdx = String(i);
    h.style.display = 'flex';
  });

  // Position after browser paint
  if (_handles.length) requestAnimationFrame(_positionAllHandles);
}

function _positionAllHandles() {
  _handles.forEach((h, i) => {
    const rightEl = TABS.find(t => t.id === _active[i + 1])?.getEl?.();
    if (!rightEl) return;
    const rect = rightEl.getBoundingClientRect();
    if (rect.left > 0) h.style.left = `${rect.left}px`;
  });
}

function _makeHandle(idx) {
  const h = document.createElement('div');
  h.className = 'ws-mob-handle';
  h.dataset.handleIdx = String(idx);
  h.setAttribute('role', 'separator');
  h.setAttribute('aria-orientation', 'vertical');
  h.setAttribute('aria-label', 'Drag to resize panels');
  h.innerHTML = '<div class="ws-mob-handle-bar"></div>';

  const onStart = (e) => {
    const hIdx = +h.dataset.handleIdx;
    _drag = { handleIdx: hIdx, startX: (e.touches ? e.touches[0] : e).clientX, startWidths: [..._widths] };
    h.classList.add('dragging');
    if (e.cancelable) e.preventDefault();
  };

  h.addEventListener('pointerdown', onStart, { passive: false });
  h.addEventListener('touchstart',  onStart, { passive: false });
  return h;
}

function _setupDragListeners() {
  const onMove = (e) => {
    if (!_drag) return;
    if (e.cancelable) e.preventDefault();

    const x  = (e.touches ? e.touches[0] : e).clientX;
    const dx = x - _drag.startX;
    const dPct = (dx / window.innerWidth) * 100;

    const { handleIdx, startWidths } = _drag;
    let L = startWidths[handleIdx]     + dPct;
    let R = startWidths[handleIdx + 1] - dPct;

    // Clamp both sides to MIN_W
    if (L < MIN_W) { R -= (MIN_W - L); L = MIN_W; }
    if (R < MIN_W) { L -= (MIN_W - R); R = MIN_W; }

    _widths = [...startWidths];
    _widths[handleIdx]     = +L.toFixed(4);
    _widths[handleIdx + 1] = +R.toFixed(4);

    _flushWidths();

    // Reposition just this handle
    const h = _handles[handleIdx];
    if (h) {
      const rightEl = TABS.find(t => t.id === _active[handleIdx + 1])?.getEl?.();
      if (rightEl) {
        requestAnimationFrame(() => {
          const rect = rightEl.getBoundingClientRect();
          if (rect.left > 0) h.style.left = `${rect.left}px`;
        });
      }
    }
  };

  const onEnd = () => {
    if (!_drag) return;
    _handles[_drag.handleIdx]?.classList.remove('dragging');
    _drag = null;
    _save();
  };

  document.addEventListener('pointermove', onMove, { passive: false });
  document.addEventListener('pointerup',   onEnd);
  document.addEventListener('touchmove',   onMove, { passive: false });
  document.addEventListener('touchend',    onEnd);
}

// ── Toggle ────────────────────────────────────────────────────────────────────

function _toggle(id) {
  const tab = TABS.find(t => t.id === id);
  if (!tab) return;

  if (_active.includes(id)) {
    if (_active.length <= 1) return; // always keep at least 1 open
    _active = _active.filter(p => p !== id);
  } else {
    if (!tab.getEl() && tab.onOpen) tab.onOpen();
    _active = [..._active, id];
  }

  _equalWidths(); // always reset to equal split on change
  _apply();
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function _buildBar() {
  const bar = document.createElement('nav');
  bar.id = 'ws-mob-tabbar';
  bar.className = 'ws-mob-tabbar';
  bar.setAttribute('role', 'tablist');
  bar.setAttribute('aria-label', 'Panel selector');

  TABS.forEach(({ id, label, icon }) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ws-mob-tab';
    btn.dataset.panel = id;
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = `<span class="ws-mob-icon" aria-hidden="true">${icon}</span><span class="ws-mob-label">${label}</span>`;
    btn.addEventListener('click', () => _toggle(id));
    bar.appendChild(btn);
  });

  return bar;
}

// ── MutationObserver: apply when panels appear late ──────────────────────────

function _watchBody() {
  const IDS = new Set(['ws-explorer-pane', 'ws-workbench-column', 'doc-editor-pane', 'ws-mob-terminal-panel']);
  const obs = new MutationObserver((mutations) => {
    for (const m of mutations)
      for (const n of m.addedNodes)
        if (n.nodeType === 1 && IDS.has(n.id)) { setTimeout(_apply, 30); return; }
  });
  obs.observe(document.body, { childList: true });
}

// ── Cleanup for desktop resize ────────────────────────────────────────────────

function _cleanupMobile() {
  document.body.classList.remove('ws-mob-view');
  TABS.forEach(({ getEl }) => {
    const el = getEl();
    if (!el) return;
    el.removeAttribute('data-mob-hidden');
    el.removeAttribute('data-mob-slot');
  });
  _handles.forEach(h => h.remove());
  _handles = [];
  if (_styleEl) _styleEl.textContent = '';
}

// ── Init ──────────────────────────────────────────────────────────────────────

export function initMobilePanels() {
  if (!window.matchMedia(`(max-width: ${BREAKPOINT}px)`).matches) return;
  _load();

  // Dedicated style tag so our widths beat !important rules in style.css
  _styleEl = document.createElement('style');
  _styleEl.id = 'ws-mob-widths';
  document.head.appendChild(_styleEl);

  document.body.classList.add('ws-mob-view');

  _bar = _buildBar();
  document.body.appendChild(_bar);

  _setupDragListeners();
  _apply();
  _watchBody();

  document.addEventListener('workspace-changed',          () => setTimeout(_apply, 50));
  document.addEventListener('workspace-environment-ready',() => setTimeout(_apply, 50));

  window.addEventListener('resize', () => {
    if (window.matchMedia(`(max-width: ${BREAKPOINT}px)`).matches) {
      setTimeout(_apply, 50);
    } else {
      _cleanupMobile();
    }
  });
}

export default { initMobilePanels };

// Auto-init
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMobilePanels);
} else {
  initMobilePanels();
}
