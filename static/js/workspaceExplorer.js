// static/js/workspaceExplorer.js
//
// Project explorer: file tree for the active workspace folder.
// File editing uses the shared document editor tab bar (document.js).

import Storage from './storage.js';
import uiModule from './ui.js';
import {
  getWorkspace,
  openWorkspaceBrowser,
  validateWorkspace,
  whenWorkspaceReady,
  isDockerWorkspace,
} from './workspace.js';
import { createWorkspaceTerminalPanel } from './workspaceTerminal.js';

const API_BASE = window.location.origin;
const STORAGE_OPEN = 'ws-explorer-open';
const _FILE_ICON = '<svg class="ws-tree-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
const _DIR_ICON = '<svg class="ws-tree-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const _DELETE_ICON = '<svg class="ws-tree-action-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const _DOWNLOAD_ICON = '<svg class="ws-tree-action-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const _IMPORT_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';

let _pane = null;
let _workbenchCol = null;
let _terminalDock = null;
let _isOpen = false;
let _workspace = '';
let _workspaceDisplay = '';
let _treePath = '';
let _expanded = new Set(['']);
let _terminal = null;

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s) : String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _docMod() {
  return window.documentModule || null;
}

function _fileTabState() {
  const mod = _docMod();
  if (mod?.getWorkspaceFileState) return mod.getWorkspaceFileState();
  return { paths: [], activePath: null, openPaths: new Set() };
}

function _openFileInEditor(detail) {
  const mod = _docMod();
  if (typeof mod?.openWorkspaceFile === 'function') {
    mod.openWorkspaceFile(detail);
    return;
  }
  document.dispatchEvent(new CustomEvent('open-workspace-file', { detail, bubbles: true }));
}

function _apiErrorDetail(err, fallback) {
  const d = err?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || x.message || JSON.stringify(x)).join('; ');
  return fallback;
}

async function _resolveWorkspaceRoot(raw) {
  const v = await validateWorkspace(raw);
  if (!v.valid || !v.path) {
    throw new Error(isDockerWorkspace()
      ? 'Workspace is not available in the container — pick a folder under /workspace'
      : 'Workspace folder not found');
  }
  _workspaceDisplay = v.display_path || v.path;
  return v.path;
}

async function _fetchList(workspace, path = '') {
  const root = await _resolveWorkspaceRoot(workspace);
  const qs = new URLSearchParams({ workspace: root, path });
  const res = await fetch(`${API_BASE}/api/workspace/list?${qs}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = _apiErrorDetail(err, `list failed: ${res.status}`);
    if (res.status === 404 && (msg === 'Not Found' || msg.includes('Not Found'))) {
      throw new Error('File tree API not loaded — restart Odysseus (docker compose restart odysseus) and hard-refresh the page.');
    }
    throw new Error(msg);
  }
  return res.json();
}

async function _fetchFile(workspace, path) {
  const root = await _resolveWorkspaceRoot(workspace);
  const qs = new URLSearchParams({ workspace: root, path });
  const res = await fetch(`${API_BASE}/api/workspace/file?${qs}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `read failed: ${res.status}`));
  }
  return res.json();
}

function _basename(relPath) {
  const parts = String(relPath || '').replace(/\\/g, '/').split('/');
  return parts[parts.length - 1] || relPath;
}

async function _downloadFile(relPath) {
  if (!_workspace) return;
  const root = await _resolveWorkspaceRoot(_workspace);
  const qs = new URLSearchParams({ workspace: root, path: relPath });
  const res = await fetch(`${API_BASE}/api/workspace/download?${qs}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `download failed: ${res.status}`));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = _basename(relPath);
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function _downloadFolder(relPath) {
  if (!_workspace) return;
  const root = await _resolveWorkspaceRoot(_workspace);
  const qs = new URLSearchParams({ workspace: root, path: relPath || '' });
  const res = await fetch(`${API_BASE}/api/workspace/download-folder?${qs}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `download failed: ${res.status}`));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${_basename(relPath) || 'workspace'}.zip`;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function _importFiles(fileList) {
  if (!_workspace || !fileList?.length) return;
  const root = await _resolveWorkspaceRoot(_workspace);
  const relDir = (_treePath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  let ok = 0;
  for (const file of fileList) {
    const fd = new FormData();
    fd.append('workspace', root);
    if (relDir) fd.append('path', relDir);
    fd.append('file', file, file.name);
    const res = await fetch(`${API_BASE}/api/workspace/import`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(_apiErrorDetail(err, `import failed: ${res.status}`));
    }
    ok += 1;
  }
  if (uiModule.showToast) {
    uiModule.showToast(ok === 1 ? `Imported ${fileList[0].name}` : `Imported ${ok} files`);
  }
  _loadTree(_treePath);
}

async function _deletePath(workspace, relPath, { recursive = false } = {}) {
  const root = await _resolveWorkspaceRoot(workspace);
  const qs = new URLSearchParams({ workspace: root, path: relPath });
  if (recursive) qs.set('recursive', '1');
  const res = await fetch(`${API_BASE}/api/workspace/file?${qs}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `delete failed: ${res.status}`));
  }
  return res.json();
}

async function _confirmDelete(name, { isDir = false, recursive = false } = {}) {
  const msg = isDir && recursive
    ? `Delete folder "${name}" and everything inside it? This cannot be undone.`
    : isDir
      ? `Delete empty folder "${name}"?`
      : `Delete "${name}"? This cannot be undone.`;
  if (uiModule?.styledConfirm) {
    return uiModule.styledConfirm(msg, { confirmText: 'Delete', danger: true });
  }
  return confirm(msg);
}

async function _deleteEntry(relPath, { isDir = false } = {}) {
  if (!_workspace) return;
  const name = _basename(relPath);
  const recursive = isDir;
  if (!await _confirmDelete(name, { isDir, recursive })) return;
  try {
    await _deletePath(_workspace, relPath, { recursive });
    _docMod()?.removeWorkspaceFileTab?.(relPath);
    if (_expanded.has(relPath)) _expanded.delete(relPath);
    if (uiModule.showToast) uiModule.showToast(`Deleted ${name}`);
    _loadTree(_treePath);
  } catch (e) {
    if (uiModule.showError) uiModule.showError(e.message || 'Delete failed');
  }
}

function _renderTreeError(msg) {
  const el = _pane?.querySelector('#ws-tree-body');
  const banner = _pane?.querySelector('#ws-tree-status');
  if (banner) {
    banner.textContent = msg;
    banner.classList.add('ws-tree-status-error');
    banner.style.display = '';
  }
  if (!el) return;
  el.innerHTML = `<div class="ws-explorer-empty"><div class="ws-explorer-empty-title">Could not load folder</div><div class="ws-explorer-empty-hint">${_esc(msg)}</div></div>`;
}

function _clearTreeError() {
  const banner = _pane?.querySelector('#ws-tree-status');
  if (banner) {
    banner.textContent = '';
    banner.classList.remove('ws-tree-status-error');
    banner.style.display = 'none';
  }
}

async function _loadTree(path = _treePath) {
  if (!_workspace || !_pane) return;
  let root = _workspace;
  try {
    const v = await validateWorkspace(_workspace);
    if (!v.valid || !v.path) {
      _renderTreeError('No workspace folder selected — open + → Workspace and choose your project folder.');
      return;
    }
    root = v.path;
    if (root !== _workspace) _workspace = root;
  } catch (_) {
    _renderTreeError('Could not verify workspace folder');
    return;
  }
  _treePath = path;
  const pathEl = _pane.querySelector('#ws-tree-path');
  if (pathEl) pathEl.textContent = path ? `/${path}` : '';
  const body = _pane.querySelector('#ws-tree-body');
  if (body) body.innerHTML = '<div class="ws-explorer-loading">Loading…</div>';
  try {
    const data = await _fetchList(root, path);
    if (data.workspace_display) {
      _workspaceDisplay = data.workspace_display;
      _syncWorkspaceLabel();
    }
    _clearTreeError();
    _renderTree(data);
  } catch (e) {
    _renderTreeError(e.message || 'Unknown error');
  }
}

function _renderTree(data) {
  const body = _pane?.querySelector('#ws-tree-body');
  if (!body) return;
  const { activePath, openPaths } = _fileTabState();
  let html = '';
  if (data.parent !== null && data.parent !== undefined) {
    html += `<div class="ws-tree-row ws-tree-up" data-action="nav" data-path="${_esc(data.parent)}">↑ ..</div>`;
  }
  for (const d of data.dirs) {
    const exp = _expanded.has(d.path);
    html += `<div class="ws-tree-row ws-tree-dir" data-action="toggle" data-path="${_esc(d.path)}">
      <span class="ws-tree-chevron">${exp ? '▾' : '▸'}</span>${_DIR_ICON}<span class="ws-tree-label">${_esc(d.name)}</span>
      <span class="ws-tree-actions">
        <button type="button" class="ws-tree-action ws-tree-download" data-action="download-folder" data-path="${_esc(d.path)}" title="Download folder as zip">${_DOWNLOAD_ICON}</button>
        <button type="button" class="ws-tree-action ws-tree-delete" data-action="delete" data-path="${_esc(d.path)}" data-is-dir="1" title="Delete folder">${_DELETE_ICON}</button>
      </span>
    </div>`;
    if (exp) {
      html += `<div class="ws-tree-children" data-parent="${_esc(d.path)}"><div class="ws-tree-loading">…</div></div>`;
    }
  }
  for (const f of data.files) {
    const active = activePath === f.path ? ' ws-tree-active' : '';
    const open = openPaths.has(f.path) ? ' ws-tree-open' : '';
    html += `<div class="ws-tree-row ws-tree-file${active}${open}" data-action="open" data-path="${_esc(f.path)}">
      ${_FILE_ICON}<span class="ws-tree-label">${_esc(f.name)}</span>
      <span class="ws-tree-actions">
        <button type="button" class="ws-tree-action ws-tree-download" data-action="download" data-path="${_esc(f.path)}" title="Download to your computer">${_DOWNLOAD_ICON}</button>
        <button type="button" class="ws-tree-action ws-tree-delete" data-action="delete" data-path="${_esc(f.path)}" title="Delete file">${_DELETE_ICON}</button>
      </span>
    </div>`;
  }
  if (!data.dirs.length && !data.files.length && data.parent === null) {
    html = '<div class="ws-explorer-empty"><div class="ws-explorer-empty-title">Empty folder</div></div>';
  } else if (!data.dirs.length && !data.files.length) {
    html += '<div class="ws-explorer-empty ws-explorer-empty-inline">No files here</div>';
  }
  body.innerHTML = html;

  body.querySelectorAll('.ws-tree-row[data-action="nav"]').forEach((row) => {
    row.addEventListener('click', () => _loadTree(row.dataset.path));
  });
  body.querySelectorAll('.ws-tree-row[data-action="toggle"]').forEach((row) => {
    row.addEventListener('click', () => _toggleDir(row.dataset.path));
  });
  body.querySelectorAll('.ws-tree-row[data-action="open"]').forEach((row) => {
    row.addEventListener('click', () => _openFileAt(row.dataset.path));
  });
  _wireTreeRowActions(body);

  for (const d of data.dirs) {
    if (_expanded.has(d.path)) _loadDirChildren(d.path);
  }
}

function _wireTreeRowActions(root) {
  root.querySelectorAll('.ws-tree-download[data-action="download"]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _downloadFile(btn.dataset.path).catch((err) => {
        if (uiModule.showError) uiModule.showError(err.message || 'Download failed');
      });
    });
  });
  root.querySelectorAll('.ws-tree-download[data-action="download-folder"]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _downloadFolder(btn.dataset.path).catch((err) => {
        if (uiModule.showError) uiModule.showError(err.message || 'Download failed');
      });
    });
  });
  root.querySelectorAll('.ws-tree-delete[data-action="delete"]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _deleteEntry(btn.dataset.path, { isDir: btn.dataset.isDir === '1' });
    });
  });
}

async function _loadDirChildren(dirPath) {
  const host = _pane?.querySelector(`.ws-tree-children[data-parent="${CSS.escape(dirPath)}"]`);
  if (!host || !_workspace) return;
  const { activePath, openPaths } = _fileTabState();
  try {
    const data = await _fetchList(_workspace, dirPath);
    let html = '';
    for (const d of data.dirs) {
      const exp = _expanded.has(d.path);
      html += `<div class="ws-tree-row ws-tree-dir ws-tree-nested" data-action="toggle" data-path="${_esc(d.path)}">
        <span class="ws-tree-chevron">${exp ? '▾' : '▸'}</span>${_DIR_ICON}<span class="ws-tree-label">${_esc(d.name)}</span>
        <span class="ws-tree-actions">
          <button type="button" class="ws-tree-action ws-tree-download" data-action="download-folder" data-path="${_esc(d.path)}" title="Download folder as zip">${_DOWNLOAD_ICON}</button>
          <button type="button" class="ws-tree-action ws-tree-delete" data-action="delete" data-path="${_esc(d.path)}" data-is-dir="1" title="Delete folder">${_DELETE_ICON}</button>
        </span>
      </div>`;
      if (exp) {
        html += `<div class="ws-tree-children" data-parent="${_esc(d.path)}"><div class="ws-tree-loading">…</div></div>`;
      }
    }
    for (const f of data.files) {
      const active = activePath === f.path ? ' ws-tree-active' : '';
      const open = openPaths.has(f.path) ? ' ws-tree-open' : '';
      html += `<div class="ws-tree-row ws-tree-file ws-tree-nested${active}${open}" data-action="open" data-path="${_esc(f.path)}">
        ${_FILE_ICON}<span class="ws-tree-label">${_esc(f.name)}</span>
        <span class="ws-tree-actions">
          <button type="button" class="ws-tree-action ws-tree-download" data-action="download" data-path="${_esc(f.path)}" title="Download to your computer">${_DOWNLOAD_ICON}</button>
          <button type="button" class="ws-tree-action ws-tree-delete" data-action="delete" data-path="${_esc(f.path)}" title="Delete file">${_DELETE_ICON}</button>
        </span>
      </div>`;
    }
    if (!html) html = '<div class="ws-tree-empty-nested">(empty)</div>';
    host.innerHTML = html;
    host.querySelectorAll('.ws-tree-row[data-action="toggle"]').forEach((row) => {
      row.addEventListener('click', (e) => { e.stopPropagation(); _toggleDir(row.dataset.path); });
    });
    host.querySelectorAll('.ws-tree-row[data-action="open"]').forEach((row) => {
      row.addEventListener('click', (e) => { e.stopPropagation(); _openFileAt(row.dataset.path); });
    });
    _wireTreeRowActions(host);
    for (const d of data.dirs) {
      if (_expanded.has(d.path)) _loadDirChildren(d.path);
    }
  } catch (e) {
    host.innerHTML = `<div class="ws-tree-error">${_esc(e.message)}</div>`;
  }
}

function _toggleDir(dirPath) {
  if (_expanded.has(dirPath)) _expanded.delete(dirPath);
  else _expanded.add(dirPath);
  _loadTree(_treePath);
}

async function _openFileAt(relPath) {
  if (!_workspace) return;
  try {
    const data = await _fetchFile(_workspace, relPath);
    try {
      _openFileInEditor({
        workspace: _workspace,
        path: data.path,
        content: data.content,
      });
    } catch (err) {
      console.error('openWorkspaceFile failed:', err);
      if (uiModule.showError) uiModule.showError(err.message || 'Could not open file in editor');
      return;
    }
    _loadTree(_treePath);
  } catch (e) {
    if (uiModule.showError) uiModule.showError(e.message || 'Could not open file');
  }
}

function _ensureMobileTerminalPanel() {
  if (document.getElementById('ws-mob-terminal-panel')) return;
  const chat = document.getElementById('chat-container');
  if (!chat) return;
  const panel = document.createElement('div');
  panel.id = 'ws-mob-terminal-panel';
  panel.className = 'ws-mob-terminal-panel';
  panel.innerHTML = `
    <div class="ws-terminal-dock-header">
      <div class="ws-terminal-dock-title-row">
        <span class="ws-terminal-dock-title">Terminal</span>
        <span class="prometheus-source-label">PROMETHEUS SOURCE</span>
      </div>
    </div>
    <div class="ws-terminal-mount" id="ws-mob-terminal-mount"></div>`;
  chat.parentNode.insertBefore(panel, chat);
}

function _terminalMountEl() {
  if (_isMobileLayout()) {
    _ensureMobileTerminalPanel();
    return document.getElementById('ws-mob-terminal-mount');
  }
  return _terminalDock?.querySelector('#ws-terminal-mount');
}

function _buildPane() {
  if (_pane) return _pane;
  _pane = document.createElement('div');
  _pane.id = 'ws-explorer-pane';
  _pane.className = 'ws-explorer-pane';
  _pane.innerHTML = `
    <div class="ws-explorer-header">
      <div class="ws-explorer-title-row">
        <span class="ws-explorer-title">Project files</span>
        <span class="prometheus-source-label">PROMETHEUS SOURCE</span>
      </div>
      <div class="ws-explorer-header-actions">
        <button type="button" class="ws-explorer-btn" id="ws-explorer-import" title="Import file from your computer">${_IMPORT_ICON}</button>
        <button type="button" class="ws-explorer-btn" id="ws-explorer-refresh" title="Refresh">↻</button>
        <button type="button" class="ws-explorer-btn" id="ws-explorer-close" title="Close panel">✕</button>
      </div>
    </div>
    <input type="file" id="ws-import-input" multiple hidden />
    <div class="ws-explorer-workspace" id="ws-explorer-workspace" title="" role="button" tabindex="0"></div>
    <div class="ws-explorer-section ws-explorer-tree-section">
      <div class="ws-explorer-section-label">
        <span class="prometheus-source-label">PROMETHEUS SOURCE</span>
        <span class="ws-tree-path" id="ws-tree-path"></span>
      </div>
      <div class="ws-tree-status" id="ws-tree-status" style="display:none"></div>
      <div class="ws-tree-body" id="ws-tree-body"></div>
    </div>`;

  _pane.querySelector('#ws-explorer-close').addEventListener('click', () => closePanel());
  _pane.querySelector('#ws-explorer-refresh').addEventListener('click', () => {
    if (_workspace) _loadTree(_treePath);
  });
  const wsLabel = _pane.querySelector('#ws-explorer-workspace');
  const openPicker = () => openWorkspaceBrowser({ fromRoot: true });
  wsLabel?.addEventListener('click', openPicker);
  wsLabel?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openPicker();
    }
  });
  _pane.querySelector('#ws-explorer-import').addEventListener('click', () => {
    if (!_workspace) {
      if (uiModule.showError) uiModule.showError('Pick a workspace folder first');
      return;
    }
    _pane.querySelector('#ws-import-input')?.click();
  });
  _pane.querySelector('#ws-import-input')?.addEventListener('change', (e) => {
    const files = e.target.files;
    if (files?.length) {
      _importFiles(files).catch((err) => {
        if (uiModule.showError) uiModule.showError(err.message || 'Import failed');
      });
    }
    e.target.value = '';
  });
  return _pane;
}

function _syncWorkspaceLabel() {
  const el = _pane?.querySelector('#ws-explorer-workspace');
  if (!el) return;
  if (_workspace) {
    const shown = _workspaceDisplay || _workspace;
    el.textContent = shown;
    el.title = `${shown} — click to change workspace folder`;
  } else if (isDockerWorkspace()) {
    el.textContent = 'No workspace — pick a folder under /workspace';
    el.title = 'Container workspace is mounted at /workspace (your Desktop)';
  } else {
    el.textContent = 'No workspace — pick a folder first';
    el.title = '';
  }
}

function _disposeTerminal() {
  if (_terminal) {
    _terminal.dispose();
    _terminal = null;
  }
}

function _renderNoWorkspaceState() {
  const body = _pane?.querySelector('#ws-tree-body');
  const pathEl = _pane?.querySelector('#ws-tree-path');
  if (pathEl) pathEl.textContent = '';
  if (!body) return;
  body.innerHTML = `
    <div class="ws-explorer-empty">
      <div class="ws-explorer-empty-title">No project folder</div>
      <div class="ws-explorer-empty-hint">${isDockerWorkspace()
        ? 'Choose a folder under <code>/workspace</code> (your Desktop mount). Host paths like <code>C:\\...</code> are not visible inside the container.'
        : 'Choose a workspace to browse files and use the terminal.'}</div>
      <button type="button" class="ws-explorer-pick-btn" id="ws-pick-workspace">Choose folder…</button>
    </div>`;
  body.querySelector('#ws-pick-workspace')?.addEventListener('click', () => openWorkspaceBrowser());
}

function _renderTerminalPlaceholder() {
  const mount = _terminalMountEl();
  if (!mount) return;
  mount.innerHTML = `
    <div class="ws-terminal-idle">
      <div class="ws-terminal-idle-title">Terminal</div>
      <div class="ws-terminal-idle-hint">Pick a workspace folder to start a shell in that directory.</div>
    </div>`;
}

function _mountTerminal() {
  _disposeTerminal();
  const mount = _terminalMountEl();
  if (!mount) return;
  if (!_workspace) {
    _renderTerminalPlaceholder();
    return;
  }
  mount.innerHTML = '';
  _terminal = createWorkspaceTerminalPanel(mount, { workspace: _workspace });
}

function _reconnectTerminal() {
  if (!_isOpen || !_workspace) return;
  if (_terminal?.reconnectAll) _terminal.reconnectAll();
  else _mountTerminal();
}

function _adoptEditorIntoWorkbench() {
  if (!_workbenchCol || !_terminalDock) return;
  const pane = document.getElementById('doc-editor-pane');
  if (!pane || pane.parentNode === _workbenchCol) return;
  _workbenchCol.insertBefore(pane, _terminalDock);
}

function _ensureWorkbenchColumn() {
  if (_isMobileLayout()) return true;
  const chat = document.getElementById('chat-container');
  if (!chat) return false;
  if (!_workbenchCol) {
    _workbenchCol = document.createElement('div');
    _workbenchCol.id = 'ws-workbench-column';
    _workbenchCol.className = 'ws-workbench-column';
    _terminalDock = document.createElement('div');
    _terminalDock.id = 'ws-terminal-dock';
    _terminalDock.className = 'ws-terminal-dock';
    _terminalDock.innerHTML = `
      <div class="ws-terminal-dock-header">
        <div class="ws-terminal-dock-title-row">
          <span class="ws-terminal-dock-title">Terminal</span>
          <span class="prometheus-source-label">PROMETHEUS SOURCE</span>
        </div>
        <span class="ws-terminal-dock-hint" title="Interactive shell for you (not the agent).">Manual shell</span>
      </div>
      <div class="ws-terminal-mount" id="ws-terminal-mount"></div>`;
    _workbenchCol.appendChild(_terminalDock);
  }
  if (!_workbenchCol.parentNode) {
    chat.parentNode.insertBefore(_workbenchCol, chat);
  }
  _adoptEditorIntoWorkbench();
  return true;
}

function _releaseWorkbench() {
  const pane = document.getElementById('doc-editor-pane');
  const chat = document.getElementById('chat-container');
  const divider = document.getElementById('doc-divider');
  if (pane && _workbenchCol && pane.parentNode === _workbenchCol && chat) {
    const sidebar = document.getElementById('sidebar');
    const isRight = sidebar && sidebar.classList.contains('right-side');
    if (isRight) {
      chat.parentNode.insertBefore(pane, chat);
    } else if (divider?.parentNode) {
      divider.after(pane);
    } else {
      chat.parentNode.insertBefore(pane, chat);
    }
  }
  _workbenchCol?.remove();
  _workbenchCol = null;
  _terminalDock = null;
}

function _ensureEditorInWorkbench() {
  if (_isMobileLayout()) return;
  const docMod = _docMod();
  if (!docMod) return;
  if (!docMod.isPanelOpen?.()) {
    docMod.openPanel?.();
  } else {
    docMod.ensurePaneMounted?.();
    _adoptEditorIntoWorkbench();
  }
}

function _mountPane() {
  const chat = document.getElementById('chat-container');
  if (!chat) return false;
  const pane = _buildPane();
  if (!pane.parentNode) {
    chat.parentNode.insertBefore(pane, chat);
  }
  if (!_ensureWorkbenchColumn()) return false;
  return true;
}

function _isMobileLayout() {
  try {
    return window.matchMedia('(max-width: 500px)').matches;
  } catch (_) {
    return false;
  }
}

async function _resolveWorkspacePath(ws) {
  const v = await validateWorkspace(ws);
  if (!v.valid || !v.path) return null;
  _workspaceDisplay = v.display_path || v.path;
  return v.path;
}

/** Reload file tree + terminal for the active workspace (IDE sync). */
async function _syncExplorerToWorkspace({ clearEditorTabs = false } = {}) {
  const ws = getWorkspace();
  if (!ws) {
    _workspace = '';
    _workspaceDisplay = '';
    _treePath = '';
    _expanded = new Set(['']);
    if (clearEditorTabs) _docMod()?.clearWorkspaceFiles?.();
    _syncWorkspaceLabel();
    _renderNoWorkspaceState();
    _mountTerminal();
    return false;
  }

  let resolved = ws;
  try {
    const path = await _resolveWorkspacePath(ws);
    if (!path) {
      _workspace = '';
      _workspaceDisplay = '';
      _syncWorkspaceLabel();
      _renderNoWorkspaceState();
      _mountTerminal();
      return false;
    }
    resolved = path;
  } catch (_) {
    if (uiModule.showError) uiModule.showError('Could not verify workspace');
    return false;
  }

  const rootChanged = _workspace !== resolved;
  _workspace = resolved;
  _treePath = '';
  _expanded = new Set(['']);
  if (clearEditorTabs || rootChanged) _docMod()?.clearWorkspaceFiles?.();
  _syncWorkspaceLabel();
  _mountTerminal();
  await _loadTree('');
  return true;
}

export async function openPanel({ promptWorkspace = false } = {}) {
  if (!_mountPane()) return;

  document.body.classList.add('ws-explorer-view');
  _isOpen = true;
  Storage.set(STORAGE_OPEN, '1');

  const overflow = document.getElementById('overflow-ws-files-btn');
  if (overflow) overflow.classList.add('active');
  _ensureEditorInWorkbench();

  const ok = await _syncExplorerToWorkspace();
  if (!ok && promptWorkspace) {
    openWorkspaceBrowser({ fromRoot: true });
    if (uiModule.showToast) uiModule.showToast('Select a workspace folder');
  }
}

export function closePanel() {
  if (!_isOpen) return;
  if (_docMod()?.hasDirtyWorkspaceFiles?.() && !confirm('Discard unsaved file changes?')) return;
  _disposeTerminal();
  _releaseWorkbench();
  document.body.classList.remove('ws-explorer-view');
  _pane?.remove();
  _pane = null;
  _isOpen = false;
  // Do not persist "closed" — desktop IDE layout (files + editor + terminal) restores on reload.
  const overflow = document.getElementById('overflow-ws-files-btn');
  if (overflow) overflow.classList.remove('active');
}

export function togglePanel() {
  if (_isOpen) closePanel();
  else openPanel();
}

async function _onWorkspaceChanged(e) {
  const path = e.detail?.path || '';
  const resync = !!e.detail?.resync;
  if (!path) {
    if (_isOpen) {
      await _syncExplorerToWorkspace({ clearEditorTabs: true });
    } else {
      _workspace = '';
      _workspaceDisplay = '';
    }
    return;
  }
  if (!_isOpen) {
    await openPanel();
    return;
  }
  await _syncExplorerToWorkspace({ clearEditorTabs: !resync });
}

function _onWorkspaceFileTabsChanged() {
  if (_isOpen && _workspace) _loadTree(_treePath);
}

/** Desktop IDE layout: project files + editor + bottom terminal. Always restored on load. */
export async function ensureIdeLayoutOpen() {
  if (_isMobileLayout()) return;
  // One-time: old builds persisted "closed" — never honor that for IDE layout.
  if (Storage.get(STORAGE_OPEN, '') === '0') Storage.remove(STORAGE_OPEN);
  await whenWorkspaceReady();
  if (!_isOpen) await openPanel();
  else {
    _ensureWorkbenchColumn();
    _ensureEditorInWorkbench();
    await _syncExplorerToWorkspace();
  }
  _docMod()?.openPanel?.();
  _docMod()?.ensurePaneMounted?.();
  _adoptEditorIntoWorkbench();
}

export function initWorkspaceExplorer() {
  document.addEventListener('workspace-changed', _onWorkspaceChanged);
  document.addEventListener('workspace-file-tabs-changed', _onWorkspaceFileTabsChanged);
  document.addEventListener('open-workspace-explorer', () => {
    openPanel().catch(() => {});
  });
  const btn = document.getElementById('overflow-ws-files-btn');
  if (btn) btn.addEventListener('click', togglePanel);
}

export default {
  initWorkspaceExplorer,
  openPanel,
  closePanel,
  togglePanel,
  ensureIdeLayoutOpen,
};
