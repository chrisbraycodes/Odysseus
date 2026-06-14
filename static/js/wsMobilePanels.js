// static/js/wsMobilePanels.js  —  Mobile 1-or-2-panel layout (viewport-driven via ideLayoutMode.js)
//
// At most 2 panels visible at once, side by side with a drag handle.
// Each tab button toggles its panel; a third selection replaces the oldest.

import { isMobileIdeLayout, IDE_LAYOUT_EVENT, IDE_LAYOUT_SYNC_EVENT } from './ideLayoutMode.js';

const LS_KEY       = 'ws-mob-v3';
const MIN_PCT      = 20;
const TAB_H        = 56;
const MAX_PANELS   = 2;

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
    getEl: () => document.getElementById('doc-editor-pane'),
    onOpen: () => { try { window.documentModule?.openPanel?.(); } catch (_) {} },
    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`,
  },
  {
    id: 'terminal', label: 'Term',
    getEl: () => document.getElementById('ws-mob-terminal-panel'),
    onOpen: () => {
      try { document.dispatchEvent(new CustomEvent('prepare-workspace-terminal')); } catch (_) {}
      try { document.dispatchEvent(new CustomEvent('open-workspace-explorer')); } catch (_) {}
    },
    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  },
];

const EMPTY_COPY = {
  files: {
    loading: { title: 'Loading files…', hint: 'Opening the project file tree.' },
  },
  editor: {
    loading: { title: 'Opening editor…', hint: 'The editor panel is loading.' },
    noFile: {
      title: 'No file open',
      hint: 'Open a file from Files, or tap + in the editor tab bar to start a new document.',
    },
  },
  terminal: {
    loading: { title: 'Loading terminal…', hint: 'Starting the workspace shell.' },
  },
};

let _panels        = ['chat'];
let _splitPct      = 50;
let _bar           = null;
let _handle        = null;
let _drag          = null;
let _active        = false;
let _wired         = false;
let _hadTerminal   = false;
let _hamburgerHome = null;
let _hamburgerNext = null;
let _applyQueued   = false;
let _saveTimer     = null;
let _lastLayoutKey = '';
let _placeholders  = {};
let _bodyObs       = null;
let _lastTermKey   = '';

function _rememberHamburgerHome() {
  const btn = document.getElementById('hamburger-btn');
  if (!btn || _hamburgerHome) return;
  _hamburgerHome = btn.parentNode;
  _hamburgerNext = btn.nextSibling;
}

function _terminalHamburgerSlot() {
  const row = document.querySelector('#ws-mob-terminal-panel .ws-terminal-dock-title-row');
  if (!row) return null;
  let slot = row.querySelector('.ws-mob-hamburger-slot');
  if (!slot) {
    slot = document.createElement('div');
    slot.className = 'ws-mob-hamburger-slot';
    row.insertBefore(slot, row.firstChild);
  }
  return slot;
}

function _syncHamburgerPlacement() {
  if (!_active) return;
  const btn = document.getElementById('hamburger-btn');
  if (!btn) return;
  _rememberHamburgerHome();

  const terminalOn = _panels.includes('terminal');
  const slot = terminalOn && document.getElementById('ws-mob-terminal-panel')
    ? _terminalHamburgerSlot()
    : null;

  if (slot) {
    if (btn.parentNode !== slot) slot.appendChild(btn);
    document.body.classList.add('ws-mob-hamburger-in-terminal');
  } else if (_hamburgerHome) {
    if (btn.parentNode !== _hamburgerHome) {
      _hamburgerHome.insertBefore(btn, _hamburgerNext);
    }
    document.body.classList.remove('ws-mob-hamburger-in-terminal');
  }
}

function _restoreHamburgerHome() {
  const btn = document.getElementById('hamburger-btn');
  if (!btn || !_hamburgerHome || btn.parentNode === _hamburgerHome) {
    document.body.classList.remove('ws-mob-hamburger-in-terminal');
    return;
  }
  _hamburgerHome.insertBefore(btn, _hamburgerNext);
  document.body.classList.remove('ws-mob-hamburger-in-terminal');
}

function _save() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ panels: _panels, splitPct: _splitPct })); } catch (_) {}
  }, 250);
}

function _load() {
  try {
    const d = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
    if (!d) return;
    if (Array.isArray(d.panels) && d.panels.length) {
      _panels = d.panels.filter((id) => TABS.some((t) => t.id === id)).slice(0, MAX_PANELS);
    }
    if (typeof d.splitPct === 'number') _splitPct = d.splitPct;
    if (!_panels.length) _panels = ['chat'];
  } catch (_) {}
}

function _clampPanels() {
  if (_panels.length > MAX_PANELS) _panels = _panels.slice(-MAX_PANELS);
  if (!_panels.length) _panels = ['chat'];
}

function _sidebarW() { return 0; }

function _closeSidebar() {
  const sb = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (sb && !sb.classList.contains('hidden')) sb.classList.add('hidden');
  if (backdrop) backdrop.classList.remove('visible');
  try { window.syncRailSide?.(); } catch (_) {}
}

function _vpH() { return window.visualViewport?.height ?? window.innerHeight; }
function _kbH() { return Math.max(0, window.innerHeight - _vpH()); }

function _layoutKey(availW, availH, kbH) {
  return `${_panels.join(',')}|${_splitPct}|${availW}|${availH}|${kbH}`;
}

/** Why a panel has no content yet — null means the panel handles its own empty UI. */
function _panelEmptyReason(id) {
  if (id === 'chat') return null;
  const el = TABS.find((t) => t.id === id)?.getEl?.();
  if (!el) return 'loading';
  if (id === 'files' || id === 'terminal') return null;
  if (id === 'editor') {
    if (!window.documentModule?.isPanelOpen?.()) return 'loading';
    const hasTab = el.querySelector('.doc-tab:not(.doc-tab-new)');
    if (!hasTab) return 'noFile';
    return null;
  }
  return null;
}

function _ensurePlaceholder(id) {
  if (!_placeholders[id]) {
    const el = document.createElement('div');
    el.className = 'ws-mob-panel-empty';
    el.dataset.panel = id;
    el.hidden = true;
    document.body.appendChild(el);
    _placeholders[id] = el;
  }
  return _placeholders[id];
}

function _syncEmptyStates(widths, availH) {
  TABS.forEach(({ id }) => {
    const ph = _placeholders[id];
    if (!_panels.includes(id)) {
      if (ph) ph.hidden = true;
      return;
    }

    const reason = _panelEmptyReason(id);
    if (!reason) {
      if (ph) ph.hidden = true;
      return;
    }

    const copy = EMPTY_COPY[id]?.[reason];
    if (!copy) {
      if (ph) ph.hidden = true;
      return;
    }

    const slot = _ensurePlaceholder(id);
    const w = widths[id] ?? window.innerWidth;
    slot.hidden = false;
    slot.style.setProperty('display', 'flex', 'important');
    slot.style.setProperty('width', `${w}px`, 'important');
    slot.style.setProperty('max-width', `${w}px`, 'important');
    slot.style.setProperty('flex', `0 0 ${w}px`, 'important');
    slot.style.setProperty('height', `${availH}px`, 'important');
    slot.style.setProperty('max-height', `${availH}px`, 'important');
    slot.innerHTML = `<div class="ws-mob-panel-empty-inner"><div class="ws-mob-panel-empty-title">${copy.title}</div><div class="ws-mob-panel-empty-hint">${copy.hint}</div></div>`;
  });
}

function _clearPlaceholders() {
  Object.values(_placeholders).forEach((el) => el.remove());
  _placeholders = {};
}

function _apply({ force = false } = {}) {
  if (!_active) return;

  _clampPanels();

  const sw     = _sidebarW();
  const kbH    = _kbH();
  const vpH    = _vpH();
  const availW = window.innerWidth - sw;
  const availH = vpH - TAB_H;
  const key    = _layoutKey(availW, availH, kbH);

  if (!force && key === _lastLayoutKey) return;
  _lastLayoutKey = key;

  const widths = {};
  if (_panels.length === 1) {
    widths[_panels[0]] = availW;
  } else {
    const leftW = Math.round(availW * _splitPct / 100);
    widths[_panels[0]] = leftW;
    widths[_panels[1]] = availW - leftW;
  }

  TABS.forEach(({ id, getEl }) => {
    const el = getEl();
    if (!el) return;

    if (_panels.includes(id)) {
      const w = widths[id];
      el.style.setProperty('display', 'flex', 'important');
      el.style.setProperty('width', `${w}px`, 'important');
      el.style.setProperty('max-width', `${w}px`, 'important');
      el.style.setProperty('min-width', '0', 'important');
      el.style.setProperty('flex', `0 0 ${w}px`, 'important');
      el.style.setProperty('height', `${availH}px`, 'important');
      el.style.setProperty('max-height', `${availH}px`, 'important');
      el.style.setProperty('overflow', 'hidden', 'important');
      el.style.setProperty('position', 'relative', 'important');
    } else {
      el.style.setProperty('display', 'none', 'important');
    }
  });

  if (_bar) _bar.style.bottom = `${kbH}px`;
  document.body.style.setProperty('height', `${availH}px`, 'important');

  const docDivider = document.getElementById('doc-divider');
  if (docDivider) docDivider.style.setProperty('display', 'none', 'important');

  if (_panels.length === 2 && _handle) {
    const x = sw + Math.round(availW * _splitPct / 100);
    _handle.style.display = 'flex';
    _handle.style.left = `${x}px`;
    _handle.style.top = '0';
    _handle.style.bottom = `${TAB_H + kbH}px`;
    _handle.style.transform = 'translateX(-50%)';
  } else if (_handle) {
    _handle.style.display = 'none';
  }

  _bar?.querySelectorAll('.ws-mob-tab').forEach((btn) => {
    const on = _panels.includes(btn.dataset.panel);
    btn.classList.toggle('ws-mob-tab-active', on);
    btn.setAttribute('aria-pressed', String(on));
  });

  _syncEmptyStates(widths, availH);
  _save();

  const hasTerminal = _panels.includes('terminal');
  const termW = hasTerminal ? (widths.terminal ?? 0) : 0;
  const termLayoutKey = `${termW}|${availH}`;
  if (hasTerminal && !_hadTerminal) {
    try { document.dispatchEvent(new CustomEvent('prepare-workspace-terminal')); } catch (_) {}
    try { document.dispatchEvent(new CustomEvent('ws-mob-terminal-show')); } catch (_) {}
    _lastTermKey = termLayoutKey;
  } else if (hasTerminal && termLayoutKey !== _lastTermKey) {
    _lastTermKey = termLayoutKey;
    try { document.dispatchEvent(new CustomEvent('ws-terminal-layout')); } catch (_) {}
  }
  _hadTerminal = hasTerminal;

  _syncHamburgerPlacement();
}

function _scheduleApply(opts = {}) {
  if (_applyQueued) return;
  _applyQueued = true;
  requestAnimationFrame(() => {
    _applyQueued = false;
    _apply(opts);
  });
}

function _toggle(id) {
  const tab = TABS.find((t) => t.id === id);
  if (!tab) return;

  const idx = _panels.indexOf(id);

  if (idx >= 0) {
    if (_panels.length <= 1) return;
    _panels.splice(idx, 1);
    _lastLayoutKey = '';
    _scheduleApply({ force: true });
    return;
  }

  if (!tab.getEl() && tab.onOpen) tab.onOpen();

  if (_panels.length >= MAX_PANELS) _panels.shift();
  _panels.push(id);
  _lastLayoutKey = '';
  _scheduleApply({ force: true });
  if (!tab.getEl()) setTimeout(() => _scheduleApply({ force: true }), 120);
}

function _makeHandle() {
  const h = document.createElement('div');
  h.className = 'ws-mob-handle ws-mob-handle-v';
  h.setAttribute('role', 'separator');
  h.setAttribute('aria-label', 'Drag to resize panels');
  h.innerHTML = '<div class="ws-mob-handle-bar"></div>';
  h.style.display = 'none';

  const start = (e) => {
    const availW = window.innerWidth - _sidebarW();
    _drag = {
      startX: (e.touches?.[0] ?? e).clientX,
      startPct: _splitPct,
      availW,
    };
    h.classList.add('dragging');
    if (e.cancelable) e.preventDefault();
  };

  h.addEventListener('pointerdown', start, { passive: false });
  h.addEventListener('touchstart', start, { passive: false });

  const move = (e) => {
    if (!_drag) return;
    if (e.cancelable) e.preventDefault();
    const dx = (e.touches?.[0] ?? e).clientX - _drag.startX;
    const dPct = (dx / _drag.availW) * 100;
    _splitPct = Math.max(MIN_PCT, Math.min(100 - MIN_PCT, _drag.startPct + dPct));
    _lastLayoutKey = '';
    _apply({ force: true });
  };

  document.addEventListener('pointermove', move, { passive: false });
  document.addEventListener('touchmove', move, { passive: false });
  document.addEventListener('pointerup', () => { if (_drag) { h.classList.remove('dragging'); _drag = null; } });
  document.addEventListener('touchend', () => { if (_drag) { h.classList.remove('dragging'); _drag = null; } });

  return h;
}

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

function _watchBody() {
  if (_bodyObs) return;
  const KNOWN = new Set(['ws-explorer-pane', 'ws-workbench-column', 'doc-editor-pane', 'ws-mob-terminal-panel']);
  _bodyObs = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1 && KNOWN.has(n.id)) {
          _lastLayoutKey = '';
          _scheduleApply({ force: true });
          return;
        }
      }
    }
  });
  _bodyObs.observe(document.body, { childList: true });
}

function _cleanup() {
  if (!_active) return;
  _active = false;
  _hadTerminal = false;
  _lastLayoutKey = '';
  _lastTermKey = '';
  clearTimeout(_saveTimer);
  _restoreHamburgerHome();
  document.body.classList.remove('ws-mob-view');
  document.body.style.removeProperty('height');
  TABS.forEach(({ getEl }) => {
    const el = getEl();
    if (!el) return;
    ['display', 'width', 'max-width', 'min-width', 'flex', 'height', 'max-height', 'overflow', 'position']
      .forEach((p) => el.style.removeProperty(p));
  });
  const docDivider = document.getElementById('doc-divider');
  if (docDivider) docDivider.style.removeProperty('display');
  _clearPlaceholders();
  _handle?.remove();
  _bar?.remove();
  _handle = null;
  _bar = null;
}

function _tabBarConnected() {
  return !!(_bar?.isConnected);
}

function _syncMountState() {
  if (isMobileIdeLayout()) {
    mountMobilePanels();
  } else if (_active) {
    unmountMobilePanels();
  }
}

function _wireGlobalListeners() {
  if (_wired) return;
  _wired = true;

  const refresh = () => {
    if (!_active) return;
    _lastLayoutKey = '';
    _scheduleApply({ force: true });
  };

  document.addEventListener('workspace-changed', refresh);
  document.addEventListener('workspace-environment-ready', refresh);
  document.addEventListener('workspace-file-tabs-changed', refresh);

  document.addEventListener(IDE_LAYOUT_EVENT, _syncMountState);
  document.addEventListener(IDE_LAYOUT_SYNC_EVENT, _syncMountState);

  let resizeTimer = null;
  const onResize = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      _syncMountState();
      if (!_active) return;
      _lastLayoutKey = '';
      _scheduleApply({ force: true });
    }, 80);
  };
  window.addEventListener('resize', onResize);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', onResize);
  }
}

export function mountMobilePanels() {
  if (!isMobileIdeLayout()) return;
  if (_active && _tabBarConnected()) {
    _scheduleApply({ force: true });
    return;
  }
  if (_active) {
    _active = false;
    _bar = null;
    _handle = null;
  }

  _load();
  _clampPanels();
  _active = true;
  document.body.classList.add('ws-mob-view');
  _closeSidebar();
  try { window.syncRailSide?.(); } catch (_) {}

  _handle = _makeHandle();
  document.body.appendChild(_handle);

  _bar = _buildBar();
  document.body.appendChild(_bar);

  _watchBody();
  _wireGlobalListeners();
  _lastLayoutKey = '';
  _scheduleApply({ force: true });
}

export function unmountMobilePanels() {
  _cleanup();
}

export function initMobilePanels() {
  _wireGlobalListeners();
  _syncMountState();
}

export default { mountMobilePanels, unmountMobilePanels, initMobilePanels };

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMobilePanels);
} else {
  initMobilePanels();
}
