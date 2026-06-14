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

function _ideRowLeft() {
  const files = document.getElementById('ws-explorer-pane');
  return files ? files.getBoundingClientRect().left : 0;
}

function _ideRowWidth() {
  const chat = document.getElementById('chat-container');
  if (!chat) return window.innerWidth - _ideRowLeft();
  const r = chat.getBoundingClientRect();
  return r.right - _ideRowLeft();
}

function _applySizes() {
  const files = document.getElementById('ws-explorer-pane');
  const chat = document.getElementById('chat-container');
  const terminal = document.getElementById('ws-terminal-dock');
  if (!terminal || !_sizes) return;

  const s = _sizes;
  if (files) {
    files.style.flex = `0 0 ${s.files}px`;
    files.style.width = `${s.files}px`;
    files.style.maxWidth = `${s.files}px`;
  }
  if (chat) {
    chat.style.flex = `0 0 ${s.chat}px`;
    chat.style.width = `${s.chat}px`;
    chat.style.maxWidth = `${s.chat}px`;
  }

  terminal.style.flex = `0 0 ${s.terminal}px`;
  terminal.style.height = `${s.terminal}px`;
  terminal.style.maxHeight = `${s.terminal}px`;
  terminal.style.minHeight = `${MIN_TERMINAL}px`;
  terminal.style.display = 'flex';
  terminal.style.visibility = 'visible';
  terminal.style.overflow = 'hidden';

  if (files) document.body.style.setProperty('--ws-files-width', `${s.files}px`);
  if (chat) document.body.style.setProperty('--ws-chat-width', `${s.chat}px`);
  document.body.style.setProperty('--ws-terminal-height', `${s.terminal}px`);
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

function _clampFiles(w) {
  const rowW = _ideRowWidth();
  const max = Math.max(MIN_FILES, rowW - MIN_CHAT - MIN_WORKBENCH - SPLIT_W * 2);
  return Math.round(Math.max(MIN_FILES, Math.min(max, w)));
}

function _clampChat(w) {
  const rowW = _ideRowWidth();
  const max = Math.max(MIN_CHAT, rowW - MIN_FILES - MIN_WORKBENCH - SPLIT_W * 2);
  return Math.round(Math.max(MIN_CHAT, Math.min(max, w)));
}

function _clampTerminal(h) {
  const workbench = document.getElementById('ws-workbench-column');
  const wbH = workbench?.getBoundingClientRect().height || 0;
  const max = wbH > 0
    ? Math.max(MIN_TERMINAL, wbH - 80)
    : Math.round(window.innerHeight * MAX_TERMINAL_VH);
  const vhCap = Math.round(window.innerHeight * MAX_TERMINAL_VH);
  return Math.round(Math.max(MIN_TERMINAL, Math.min(max, vhCap, h)));
}

function _startDrag(handle, kind, e) {
  if (e.button !== 0 && e.pointerType === 'mouse') return;
  e.preventDefault();
  const files = document.getElementById('ws-explorer-pane');
  const chat = document.getElementById('chat-container');
  const terminal = document.getElementById('ws-terminal-dock');
  if (!terminal) return;

  _sizes = _sizes || _loadSizes();
  _drag = {
    kind,
    handle,
    pointerId: e.pointerId,
    filesLeft: files?.getBoundingClientRect().left ?? _ideRowLeft(),
    chatRight: chat?.getBoundingClientRect().right ?? (window.innerWidth - 8),
    workbenchBottom: document.getElementById('ws-workbench-column')?.getBoundingClientRect().bottom
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
  const files = document.getElementById('ws-explorer-pane');
  const workbench = document.getElementById('ws-workbench-column');
  const chat = document.getElementById('chat-container');
  const terminal = document.getElementById('ws-terminal-dock');
  if (!files || !workbench || !chat || !terminal) return false;

  const hFiles = _ensureHandle('ws-split-files', 'v');
  const hChat = _ensureHandle('ws-split-chat', 'v');
  const hTerm = _ensureHandle('ws-split-terminal', 'h');

  if (hFiles.previousElementSibling !== files) files.after(hFiles);
  if (hChat.previousElementSibling !== workbench) workbench.after(hChat);
  if (hTerm.nextElementSibling !== terminal) workbench.insertBefore(hTerm, terminal);

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

  if (!_onMove) {
    _onMove = (e) => _moveDrag(e);
    _onUp = (e) => _endDrag(e);
    document.addEventListener('pointermove', _onMove);
    document.addEventListener('pointerup', _onUp);
    document.addEventListener('pointercancel', _onUp);
  }
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
