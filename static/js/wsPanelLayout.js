// static/js/wsPanelLayout.js — Desktop IDE panel drag-to-swap (4 zones, swap on drop).
//
// Zones: left (full height) | centerTop + centerBottom | right (full height)
// Default: files | editor + terminal | chat

import { isDesktopIdeLayout, IDE_LAYOUT_EVENT } from './ideLayoutMode.js';
import { refreshWsPanelResize } from './wsPanelResize.js';

const STORAGE_KEY = 'ws-ide-panel-zones-v1';

const ZONES = ['left', 'centerTop', 'centerBottom', 'right'];

const ZONE_GRID = {
  left: 'ws-zone-left',
  centerTop: 'ws-zone-centerTop',
  centerBottom: 'ws-zone-centerBottom',
  right: 'ws-zone-right',
};

const PANELS = {
  files: {
    id: 'ws-explorer-pane',
    label: 'Project files',
    getEl: () => document.getElementById('ws-explorer-pane'),
    gripSelector: '.ws-explorer-header .ws-explorer-title-row',
  },
  editor: {
    id: 'doc-editor-pane',
    label: 'Editor',
    getEl: () => document.getElementById('doc-editor-pane'),
    gripSelector: '.doc-tab-bar',
  },
  terminal: {
    id: 'ws-terminal-dock',
    label: 'Terminal',
    getEl: () => document.getElementById('ws-terminal-dock'),
    gripSelector: '.ws-terminal-dock-header',
  },
  chat: {
    id: 'chat-container',
    label: 'Chat',
    getEl: () => document.getElementById('chat-container'),
    gripSelector: '.chat-meta-overlay',
    gripBefore: '#current-meta',
  },
};

const DEFAULT_ASSIGN = {
  left: 'files',
  centerTop: 'editor',
  centerBottom: 'terminal',
  right: 'chat',
};

let _mounted = false;
let _assign = null;
let _grid = null;
let _drag = null;
let _overlay = null;

function _loadAssign() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (!raw || typeof raw !== 'object') return { ...DEFAULT_ASSIGN };
    const panelIds = Object.keys(PANELS);
    const assigned = ZONES.map((z) => raw[z]).filter((p) => PANELS[p]);
    const unique = [...new Set(assigned)];
    if (unique.length !== panelIds.length) return { ...DEFAULT_ASSIGN };
    for (const p of panelIds) {
      if (!unique.includes(p)) return { ...DEFAULT_ASSIGN };
    }
    const out = {};
    for (const z of ZONES) out[z] = raw[z];
    return out;
  } catch (_) {
    return { ...DEFAULT_ASSIGN };
  }
}

function _saveAssign() {
  if (!_assign) return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(_assign)); } catch (_) {}
}

function _panelZone(panelId) {
  if (!_assign) return null;
  return ZONES.find((z) => _assign[z] === panelId) || null;
}

function _zonePanel(zone) {
  return _assign?.[zone] || null;
}

function _ensureGrid() {
  if (_grid?.isConnected) return _grid;
  const chat = document.getElementById('chat-container');
  if (!chat?.parentNode) return null;

  _grid = document.getElementById('ws-ide-desktop-grid');
  if (!_grid) {
    _grid = document.createElement('div');
    _grid.id = 'ws-ide-desktop-grid';
    _grid.className = 'ws-ide-desktop-grid';
    chat.parentNode.insertBefore(_grid, chat);
  }

  for (const { getEl } of Object.values(PANELS)) {
    const el = getEl();
    if (el && el.parentNode !== _grid) _grid.appendChild(el);
  }

  document.getElementById('ws-workbench-column')?.remove();
  try { document.dispatchEvent(new CustomEvent('ws-panel-layout-mounted')); } catch (_) {}
  return _grid;
}

function _applyGridAreas() {
  if (!_assign) return;
  for (const zone of ZONES) {
    const panelId = _assign[zone];
    const el = PANELS[panelId]?.getEl?.();
    if (!el) continue;
    el.style.gridArea = ZONE_GRID[zone];
    el.dataset.wsZone = zone;
  }
}

function _removeGrips() {
  document.querySelectorAll('.ws-panel-layout-grip').forEach((el) => el.remove());
}

function _ensureGrip(panelId) {
  const cfg = PANELS[panelId];
  const root = cfg?.getEl?.();
  if (!root) return;
  const host = root.querySelector(cfg.gripSelector) || root;
  if (host.querySelector(':scope > .ws-panel-layout-grip')) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ws-panel-layout-grip';
  btn.title = `Drag to move ${cfg.label}`;
  btn.setAttribute('aria-label', `Drag to move ${cfg.label}`);
  btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>';
  btn.addEventListener('pointerdown', (e) => _startDrag(panelId, e));

  const anchor = cfg.gripBefore ? host.querySelector(cfg.gripBefore) : null;
  if (anchor) host.insertBefore(btn, anchor);
  else host.prepend(btn);
}

function _wireGrips() {
  document.querySelector('.chat-top-bar > .ws-panel-layout-grip')?.remove();
  for (const panelId of Object.keys(PANELS)) _ensureGrip(panelId);
}

function _ensureOverlay() {
  if (_overlay?.isConnected) return _overlay;
  _overlay = document.createElement('div');
  _overlay.id = 'ws-panel-layout-overlay';
  _overlay.className = 'ws-panel-layout-overlay';
  _overlay.hidden = true;
  for (const zone of ZONES) {
    const slot = document.createElement('div');
    slot.className = 'ws-panel-layout-slot';
    slot.dataset.zone = zone;
    slot.innerHTML = `<span class="ws-panel-layout-slot-label"></span>`;
    slot.addEventListener('pointerup', (e) => _dropOnZone(zone, e));
    _overlay.appendChild(slot);
  }
  document.body.appendChild(_overlay);
  return _overlay;
}

function _slotLabel(zone) {
  const p = _zonePanel(zone);
  return PANELS[p]?.label || zone;
}

function _showOverlay(panelId) {
  const ov = _ensureOverlay();
  ov.hidden = false;
  ov.dataset.dragPanel = panelId;
  for (const slot of ov.querySelectorAll('.ws-panel-layout-slot')) {
    const zone = slot.dataset.zone;
    const label = slot.querySelector('.ws-panel-layout-slot-label');
    if (label) label.textContent = _slotLabel(zone);
    slot.classList.toggle('ws-panel-layout-slot-source', _panelZone(panelId) === zone);
    slot.classList.remove('ws-panel-layout-slot-hover');
  }
  _positionOverlaySlots();
}

function _hideOverlay() {
  if (_overlay) {
    _overlay.hidden = true;
    delete _overlay.dataset.dragPanel;
  }
}

function _zoneRect(zone) {
  const el = document.querySelector(`[data-ws-zone="${zone}"]`);
  return el?.getBoundingClientRect() || null;
}

function _positionOverlaySlots() {
  if (!_overlay || _overlay.hidden || !_grid) return;
  const gridRect = _grid.getBoundingClientRect();
  _overlay.style.setProperty('--grid-left', `${gridRect.left}px`);
  _overlay.style.setProperty('--grid-top', `${gridRect.top}px`);
  _overlay.style.setProperty('--grid-width', `${gridRect.width}px`);
  _overlay.style.setProperty('--grid-height', `${gridRect.height}px`);

  const leftR = _zoneRect('left');
  const rightR = _zoneRect('right');
  const topR = _zoneRect('centerTop');
  const bottomR = _zoneRect('centerBottom');

  const leftW = leftR?.width
    || parseInt(getComputedStyle(document.body).getPropertyValue('--ws-files-width'), 10)
    || 340;
  const rightW = rightR?.width
    || parseInt(getComputedStyle(document.body).getPropertyValue('--ws-chat-width'), 10)
    || 380;
  const topH = topR?.height || Math.max(80, gridRect.height * 0.65);
  const bottomH = bottomR?.height
    || parseInt(getComputedStyle(document.body).getPropertyValue('--ws-terminal-height'), 10)
    || 280;
  const centerW = topR?.width || Math.max(120, gridRect.width - leftW - rightW);

  _overlay.style.setProperty('--slot-files-w', `${leftW}px`);
  _overlay.style.setProperty('--slot-chat-w', `${rightW}px`);
  _overlay.style.setProperty('--slot-center-w', `${centerW}px`);
  _overlay.style.setProperty('--slot-top-h', `${topH}px`);
  _overlay.style.setProperty('--slot-term-h', `${bottomH}px`);
}

function _zoneAtPoint(x, y) {
  if (!_grid) return null;
  const gridRect = _grid.getBoundingClientRect();
  if (x < gridRect.left || x > gridRect.right || y < gridRect.top || y > gridRect.bottom) {
    return null;
  }

  for (const zone of ['left', 'right', 'centerTop', 'centerBottom']) {
    const r = _zoneRect(zone);
    if (!r) continue;
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return zone;
  }
  return null;
}

function _startDrag(panelId, e) {
  if (!isDesktopIdeLayout() || !_mounted) return;
  if (e.button !== 0 && e.pointerType === 'mouse') return;
  e.preventDefault();
  e.stopPropagation();
  _drag = { panelId, pointerId: e.pointerId, grip: e.currentTarget };
  document.body.classList.add('ws-panel-layout-dragging');
  _showOverlay(panelId);
  e.currentTarget.setPointerCapture?.(e.pointerId);

  const onMove = (ev) => {
    if (!_drag || ev.pointerId !== _drag.pointerId) return;
    _positionOverlaySlots();
    const zone = _zoneAtPoint(ev.clientX, ev.clientY);
    for (const slot of _overlay.querySelectorAll('.ws-panel-layout-slot')) {
      slot.classList.toggle('ws-panel-layout-slot-hover', slot.dataset.zone === zone);
    }
  };
  const onUp = (ev) => {
    if (!_drag || ev.pointerId !== _drag.pointerId) return;
    try { _drag.grip?.releasePointerCapture?.(_drag.pointerId); } catch (_) {}
    const zone = _zoneAtPoint(ev.clientX, ev.clientY);
    if (zone) _swapPanels(_drag.panelId, zone);
    _drag = null;
    _hideOverlay();
    document.body.classList.remove('ws-panel-layout-dragging');
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
    document.removeEventListener('pointercancel', onUp);
  };
  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onUp);
  document.addEventListener('pointercancel', onUp);
}

function _dropOnZone(zone, e) {
  if (!_drag) return;
  e.preventDefault();
  _swapPanels(_drag.panelId, zone);
  _drag = null;
  _hideOverlay();
  document.body.classList.remove('ws-panel-layout-dragging');
}

function _swapPanels(dragPanelId, targetZone) {
  const sourceZone = _panelZone(dragPanelId);
  if (!sourceZone || !targetZone || sourceZone === targetZone) return;
  const otherPanel = _zonePanel(targetZone);
  if (!otherPanel) return;
  _assign[sourceZone] = otherPanel;
  _assign[targetZone] = dragPanelId;
  _saveAssign();
  _applyGridAreas();
  refreshWsPanelResize();
  requestAnimationFrame(() => {
    try { document.dispatchEvent(new CustomEvent('ws-terminal-layout')); } catch (_) {}
    try { window.dispatchEvent(new Event('resize')); } catch (_) {}
  });
}

export function applyWsPanelLayout() {
  if (!_assign) _assign = _loadAssign();
  if (!_ensureGrid()) return false;
  document.body.classList.add('ws-ide-grid-layout');
  _applyGridAreas();
  _wireGrips();
  return true;
}

export function mountWsPanelLayout() {
  if (!isDesktopIdeLayout()) return;
  if (!document.body.classList.contains('ws-explorer-view')) return;
  _assign = _assign || _loadAssign();
  if (!applyWsPanelLayout()) return;
  _mounted = true;
  refreshWsPanelResize();
}

export function unmountWsPanelLayout() {
  _hideOverlay();
  _drag = null;
  _removeGrips();
  document.body.classList.remove('ws-ide-grid-layout', 'ws-panel-layout-dragging');
  if (_grid) {
    const chat = document.getElementById('chat-container');
    const parent = chat?.parentNode || document.body;
    for (const { getEl } of Object.values(PANELS)) {
      const el = getEl();
      if (el && _grid.contains(el)) parent.insertBefore(el, _grid.nextSibling);
    }
    _grid.remove();
    _grid = null;
  }
  for (const { getEl } of Object.values(PANELS)) {
    const el = getEl();
    if (el) {
      el.style.gridArea = '';
      delete el.dataset.wsZone;
    }
  }
  _overlay?.remove();
  _overlay = null;
  _mounted = false;
}

export function refreshWsPanelLayout() {
  if (!isDesktopIdeLayout() || !document.body.classList.contains('ws-explorer-view')) {
    unmountWsPanelLayout();
    return;
  }
  if (!_mounted) {
    mountWsPanelLayout();
    return;
  }
  applyWsPanelLayout();
  refreshWsPanelResize();
}

export function resetWsPanelLayout() {
  _assign = { ...DEFAULT_ASSIGN };
  _saveAssign();
  refreshWsPanelLayout();
}

if (typeof document !== 'undefined') {
  document.addEventListener(IDE_LAYOUT_EVENT, () => {
    if (isDesktopIdeLayout()) refreshWsPanelLayout();
    else unmountWsPanelLayout();
  });
}
