// static/js/wsPanelResize.js — drag-to-resize handles for the desktop IDE layout
// (project files | editor | terminal | chat).

import { isDesktopIdeLayout, IDE_LAYOUT_EVENT } from './ideLayoutMode.js';

const STORAGE_KEY = 'ws-ide-layout-v1';
const MIN_FILES = 200;
const MIN_CHAT = 280;
const MIN_WORKBENCH = 280;
const MIN_TERMINAL = 120;
const MAX_TERMINAL_VH = 0.6;
const SPLIT_W = 6;

const DEFAULTS = { files: 340, chat: 380, terminal: 280 };

let _mounted = false;
let _drag = null;
let _sizes = null;
let _onMove = null;
let _onUp = null;
let _onWinResize = null;

function _isDesktop() {
  return isDesktopIdeLayout();
}

function _loadSizes() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (!raw || typeof raw !== 'object') return { ...DEFAULTS };
    const out = { ...DEFAULTS };
    for (const k of Object.keys(DEFAULTS)) {
      const n = Number(raw[k]);
      if (Number.isFinite(n) && n > 0) out[k] = Math.round(n);
    }
    return out;
  } catch (_) {
    return { ...DEFAULTS };
  }
}

function _saveSizes() {
  if (!_sizes) return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(_sizes)); } catch (_) {}
}

function _gridRoot() {
  return document.getElementById('ws-ide-desktop-grid');
}

function _zoneEl(zone) {
  return document.querySelector(`[data-ws-zone="${zone}"]`);
}

function _ideRowLeft() {
  const grid = _gridRoot();
  if (grid) return grid.getBoundingClientRect().left;
  const files = document.getElementById('ws-explorer-pane');
  return files ? files.getBoundingClientRect().left : 0;
}

function _ideRowWidth() {
  const grid = _gridRoot();
  if (grid) return grid.getBoundingClientRect().width;
  const chat = document.getElementById('chat-container');
  if (!chat) return window.innerWidth - _ideRowLeft();
  const r = chat.getBoundingClientRect();
  return r.right - _ideRowLeft();
}

function _applySizes() {
  const grid = _gridRoot();
  const leftEl = _zoneEl('left') || document.getElementById('ws-explorer-pane');
  const rightEl = _zoneEl('right') || document.getElementById('chat-container');
  const bottomEl = _zoneEl('centerBottom') || document.getElementById('ws-terminal-dock');
  if (!bottomEl || !_sizes) return;

  const s = _sizes;
  document.body.style.setProperty('--ws-files-width', `${s.files}px`);
  document.body.style.setProperty('--ws-chat-width', `${s.chat}px`);
  document.body.style.setProperty('--ws-terminal-height', `${s.terminal}px`);

  if (grid) {
    bottomEl.style.minHeight = `${MIN_TERMINAL}px`;
    bottomEl.style.display = 'flex';
    bottomEl.style.visibility = 'visible';
    bottomEl.style.overflow = 'hidden';
    bottomEl.style.flex = '';
    bottomEl.style.height = '';
    bottomEl.style.maxHeight = '';
    if (leftEl) {
      leftEl.style.flex = '';
      leftEl.style.width = '';
      leftEl.style.maxWidth = '';
    }
    if (rightEl) {
      rightEl.style.flex = '';
      rightEl.style.width = '';
      rightEl.style.maxWidth = '';
    }
    return;
  }

  if (leftEl) {
    leftEl.style.flex = `0 0 ${s.files}px`;
    leftEl.style.width = `${s.files}px`;
    leftEl.style.maxWidth = `${s.files}px`;
  }
  if (rightEl) {
    rightEl.style.flex = `0 0 ${s.chat}px`;
    rightEl.style.width = `${s.chat}px`;
    rightEl.style.maxWidth = `${s.chat}px`;
  }

  bottomEl.style.flex = `0 0 ${s.terminal}px`;
  bottomEl.style.height = `${s.terminal}px`;
  bottomEl.style.maxHeight = `${s.terminal}px`;
  bottomEl.style.minHeight = `${MIN_TERMINAL}px`;
  bottomEl.style.display = 'flex';
  bottomEl.style.visibility = 'visible';
  bottomEl.style.overflow = 'hidden';
}

function _applyTerminalSizeOnly() {
  const terminal = document.getElementById('ws-terminal-dock');
  if (!terminal) return false;
  _sizes = _sizes || _loadSizes();
  _sizes.terminal = _clampTerminal(_sizes.terminal);
  const h = _sizes.terminal;
  terminal.style.flex = `0 0 ${h}px`;
  terminal.style.height = `${h}px`;
  terminal.style.maxHeight = `${h}px`;
  terminal.style.minHeight = `${MIN_TERMINAL}px`;
  terminal.style.display = 'flex';
  terminal.style.visibility = 'visible';
  document.body.style.setProperty('--ws-terminal-height', `${h}px`);
  return true;
}

function _clearPanelSizes() {
  for (const id of ['ws-explorer-pane', 'chat-container', 'ws-terminal-dock']) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.style.flex = '';
    el.style.width = '';
    el.style.maxWidth = '';
    el.style.height = '';
    el.style.maxHeight = '';
  }
  document.body.style.removeProperty('--ws-files-width');
  document.body.style.removeProperty('--ws-chat-width');
  document.body.style.removeProperty('--ws-terminal-height');
}

function _ensureHandle(id, orientation) {
  let el = document.getElementById(id);
  if (el) return el;
  el = document.createElement('div');
  el.id = id;
  el.className = `ws-ide-split ws-ide-split-${orientation}`;
  el.setAttribute('role', 'separator');
  el.setAttribute('aria-orientation', orientation === 'h' ? 'horizontal' : 'vertical');
  el.setAttribute('data-no-swipe-dismiss', '1');
  el.title = 'Drag to resize';
  el.innerHTML = '<div class="ws-ide-split-grip"></div>';
  return el;
}

function _compactDesktop() {
  return window.innerWidth > 768 && window.innerWidth <= 1100;
}

function _minFiles() { return _compactDesktop() ? 160 : MIN_FILES; }
function _minChat() { return _compactDesktop() ? 220 : MIN_CHAT; }
function _minWorkbench() { return _compactDesktop() ? 180 : MIN_WORKBENCH; }

function _clampFiles(w) {
  const rowW = _ideRowWidth();
  const minF = _minFiles();
  const max = Math.max(minF, rowW - _minChat() - _minWorkbench() - SPLIT_W * 2);
  return Math.round(Math.max(minF, Math.min(max, w)));
}

function _clampChat(w) {
  const rowW = _ideRowWidth();
  const minC = _minChat();
  const max = Math.max(minC, rowW - _minFiles() - _minWorkbench() - SPLIT_W * 2);
  return Math.round(Math.max(minC, Math.min(max, w)));
}

function _clampTerminal(h) {
  const grid = _gridRoot();
  const wbH = grid?.getBoundingClientRect().height
    || document.getElementById('ws-workbench-column')?.getBoundingClientRect().height
    || 0;
  const max = wbH > 0
    ? Math.max(MIN_TERMINAL, wbH - 80)
    : Math.round(window.innerHeight * MAX_TERMINAL_VH);
  const vhCap = Math.round(window.innerHeight * MAX_TERMINAL_VH);
  return Math.round(Math.max(MIN_TERMINAL, Math.min(max, vhCap, h)));
}

function _ensureResizeListeners() {
  if (_onMove) return;
  _onMove = (e) => _moveDrag(e);
  _onUp = (e) => _endDrag(e);
  document.addEventListener('pointermove', _onMove);
  document.addEventListener('pointerup', _onUp);
  document.addEventListener('pointercancel', _onUp);
}

function _startDrag(handle, kind, e) {
  if (e.button !== 0 && e.pointerType === 'mouse') return;
  e.preventDefault();
  const grid = _gridRoot();
  const files = _zoneEl('left') || document.getElementById('ws-explorer-pane');
  const chat = _zoneEl('right') || document.getElementById('chat-container');
  const terminal = _zoneEl('centerBottom') || document.getElementById('ws-terminal-dock');
  if (!terminal) return;

  _sizes = _sizes || _loadSizes();
  const gridRect = grid?.getBoundingClientRect();
  _drag = {
    kind,
    handle,
    pointerId: e.pointerId,
    filesLeft: files?.getBoundingClientRect().left ?? _ideRowLeft(),
    chatRight: chat?.getBoundingClientRect().right ?? (window.innerWidth - 8),
    workbenchBottom: gridRect?.bottom
      ?? document.getElementById('ws-workbench-column')?.getBoundingClientRect().bottom
      ?? terminal.getBoundingClientRect().bottom,
  };
  handle.classList.add('ws-ide-split-dragging');
  handle.setPointerCapture?.(e.pointerId);
  document.body.style.userSelect = 'none';
  document.body.style.cursor = kind === 'terminal' ? 'row-resize' : 'col-resize';
}

function _moveDrag(e) {
  if (!_drag || e.pointerId !== _drag.pointerId) return;
  if (!_sizes) return;

  if (_drag.kind === 'files') {
    _sizes.files = _clampFiles(e.clientX - _drag.filesLeft);
  } else if (_drag.kind === 'chat') {
    _sizes.chat = _clampChat(_drag.chatRight - e.clientX);
  } else if (_drag.kind === 'terminal') {
    _sizes.terminal = _clampTerminal(_drag.workbenchBottom - e.clientY);
  }
  _applySizes();
}

function _endDrag(e) {
  if (!_drag || (e && e.pointerId !== _drag.pointerId)) return;
  const { handle, pointerId } = _drag;
  try { handle.releasePointerCapture?.(pointerId); } catch (_) {}
  handle.classList.remove('ws-ide-split-dragging');
  document.body.style.userSelect = '';
  document.body.style.cursor = '';
  _drag = null;
  _saveSizes();
  requestAnimationFrame(() => {
    try { window.dispatchEvent(new Event('resize')); } catch (_) {}
  });
}

function _wireHandle(handle, kind) {
  if (handle.dataset.wsResizeWired === '1') return;
  handle.dataset.wsResizeWired = '1';
  handle.addEventListener('pointerdown', (e) => _startDrag(handle, kind, e));
  handle.addEventListener('dblclick', () => {
    _sizes = { ...DEFAULTS };
    _applySizes();
    _saveSizes();
    requestAnimationFrame(() => {
      try { window.dispatchEvent(new Event('resize')); } catch (_) {}
    });
  });
}

function _reclampSizes() {
  if (!_sizes) return;
  _sizes.files = _clampFiles(_sizes.files);
  _sizes.chat = _clampChat(_sizes.chat);
  _sizes.terminal = _clampTerminal(_sizes.terminal);
  _applySizes();
}

function _placeHandles() {
  const grid = _gridRoot();
  const leftEl = _zoneEl('left') || document.getElementById('ws-explorer-pane');
  const rightEl = _zoneEl('right') || document.getElementById('chat-container');
  const centerTop = _zoneEl('centerTop') || document.getElementById('doc-editor-pane');
  const centerBottom = _zoneEl('centerBottom') || document.getElementById('ws-terminal-dock');
  const workbench = document.getElementById('ws-workbench-column');
  const parent = grid || leftEl?.parentNode;
  if (!leftEl || !rightEl || !centerBottom || !parent) return false;

  const hFiles = _ensureHandle('ws-split-files', 'v');
  const hChat = _ensureHandle('ws-split-chat', 'v');
  const hTerm = _ensureHandle('ws-split-terminal', 'h');

  if (grid) {
    if (!grid.contains(hFiles)) grid.appendChild(hFiles);
    if (!grid.contains(hChat)) grid.appendChild(hChat);
    if (!grid.contains(hTerm)) grid.appendChild(hTerm);
    hFiles.style.gridArea = 'ws-zone-split-v';
    hChat.style.gridArea = 'ws-zone-split-v2';
    hTerm.style.gridArea = 'ws-zone-split-h';
  } else if (workbench) {
    if (hFiles.previousElementSibling !== leftEl) leftEl.after(hFiles);
    if (hChat.previousElementSibling !== workbench) workbench.after(hChat);
    if (hTerm.nextElementSibling !== centerBottom) workbench.insertBefore(hTerm, centerBottom);
  } else {
    if (hFiles.previousElementSibling !== leftEl) leftEl.after(hFiles);
    if (centerTop && hTerm.nextElementSibling !== centerBottom) centerTop.after(hTerm);
    if (hChat.nextElementSibling !== rightEl) rightEl.before(hChat);
  }

  _wireHandle(hFiles, 'files');
  _wireHandle(hChat, 'chat');
  _wireHandle(hTerm, 'terminal');
  return true;
}

export function mountWsPanelResize() {
  if (!_isDesktop()) return;

  _sizes = _sizes || _loadSizes();
  _applyTerminalSizeOnly();
  _mounted = true;

  if (!_placeHandles()) {
    _reclampSizes();
    return;
  }

  _applySizes();

  _ensureResizeListeners();
  if (!_onWinResize) {
    _onWinResize = () => _reclampSizes();
    window.addEventListener('resize', _onWinResize);
  }
}

export function unmountWsPanelResize() {
  _endDrag();
  if (_onWinResize) {
    window.removeEventListener('resize', _onWinResize);
    _onWinResize = null;
  }
  for (const id of ['ws-split-files', 'ws-split-chat', 'ws-split-terminal']) {
    document.getElementById(id)?.remove();
  }
  _clearPanelSizes();
  _mounted = false;
}

export function refreshWsPanelResize() {
  if (!_isDesktop()) return;
  _sizes = _sizes || _loadSizes();
  if (!_mounted) {
    _applyTerminalSizeOnly();
    if (_placeHandles()) _mounted = true;
  }
  if (!_mounted) return;
  _placeHandles();
  _reclampSizes();
  _ensureResizeListeners();
  requestAnimationFrame(() => {
    try { window.dispatchEvent(new Event('resize')); } catch (_) {}
  });
}

if (typeof document !== 'undefined') {
  document.addEventListener(IDE_LAYOUT_EVENT, () => {
    if (isDesktopIdeLayout()) refreshWsPanelResize();
    else unmountWsPanelResize();
  });
}
