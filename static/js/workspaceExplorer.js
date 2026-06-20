// static/js/workspaceExplorer.js
//
// Project explorer: file tree for the active workspace folder.
// File editing uses the shared document editor tab bar (document.js).
//
// ⚠ LAYOUT CONTRACT: AGENTS.md + docs/workspace-ide-layout.md
// Desktop: file tree, editor, and terminal must ALWAYS stay visible.

import Storage from './storage.js';
import uiModule from './ui.js';
import {
  getVerifiedWorkspace,
  ensureVerifiedWorkspace,
  openWorkspaceBrowser,
  whenWorkspaceReady,
  isDockerWorkspace,
} from './workspace.js';
import { createWorkspaceTerminalPanel } from './workspaceTerminal.js';
import { mountWsPanelResize, unmountWsPanelResize, refreshWsPanelResize } from './wsPanelResize.js';
import { mountWsPanelLayout, unmountWsPanelLayout, refreshWsPanelLayout } from './wsPanelLayout.js';
import { isMobileIdeLayout, IDE_LAYOUT_EVENT } from './ideLayoutMode.js';
import { openWorkspaceSavePicker } from './wsEditorSave.js';
import documentModule from './document.js';

const API_BASE = window.location.origin;
const STORAGE_OPEN = 'ws-explorer-open';
const _FILE_ICON = '<svg class="ws-tree-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
const _DIR_ICON = '<svg class="ws-tree-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const _DELETE_ICON = '<svg class="ws-tree-action-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const _DOWNLOAD_ICON = '<svg class="ws-tree-action-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const _IMPORT_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
const _NEW_FILE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
const _NEW_FOLDER_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>';

let _pane = null;
let _workbenchCol = null;
let _terminalDock = null;
let _isOpen = false;
let _treePath = '';
let _expanded = new Set(['']);
let _terminal = null;
let _lastSyncedRoot = '';
let _loadTreeGen = 0;
let _loadTreeInflight = null;
let _restoreIdeInflight = null;

function _syncTreeFileTabState() {
  const body = _pane?.querySelector('#ws-tree-body');
  if (!body) return;
  const { activePath, openPaths } = _fileTabState();
  body.querySelectorAll('.ws-tree-row.ws-tree-file').forEach((row) => {
    const p = row.dataset.path || '';
    row.classList.toggle('ws-tree-active', p === activePath);
    row.classList.toggle('ws-tree-open', openPaths.has(p));
  });
}

function _treeBodyEl() {
  return _pane?.querySelector('#ws-tree-body');
}

/** True once the tree body has any rendered state (files, empty folder, or error). */
function _treeHasContent() {
  const body = _treeBodyEl();
  if (!body) return false;
  return !!(
    body.querySelector('.ws-tree-row')
    || body.querySelector('.ws-explorer-empty')
    || body.querySelector('.ws-tree-error')
  );
}

function _treeBodyIsBlank() {
  const body = _treeBodyEl();
  return !body || body.childElementCount === 0;
}

function _wsRoot() {
  return getVerifiedWorkspace()?.path || '';
}

function _wsDisplay() {
  const v = getVerifiedWorkspace();
  return v?.displayPath || v?.path || '';
}

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s) : String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _docMod() {
  return window.documentModule || documentModule || null;
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
  } else {
    document.dispatchEvent(new CustomEvent('open-workspace-file', { detail, bubbles: true }));
    if (!_isMobileLayout()) _ensureEditorInWorkbench();
  }
  if (_isMobileLayout()) {
    try {
      document.dispatchEvent(new CustomEvent('ws-mob-focus-panel', { detail: { panel: 'editor' } }));
    } catch (_) {}
  }
}

function _apiErrorDetail(err, fallback) {
  const d = err?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || x.message || JSON.stringify(x)).join('; ');
  return fallback;
}

async function _verifiedRoot() {
  const v = await ensureVerifiedWorkspace();
  if (!v?.path) {
    throw new Error(isDockerWorkspace()
      ? 'Workspace is not available in the container — pick a folder under /workspace'
      : 'Workspace folder not found');
  }
  return v.path;
}

async function _fetchList(workspace, path = '') {
  const root = workspace || await _verifiedRoot();
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
  const root = workspace || await _verifiedRoot();
  const qs = new URLSearchParams({ workspace: root, path });
  const res = await fetch(`${API_BASE}/api/workspace/file?${qs}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `read failed: ${res.status}`));
  }
  return res.json();
}

async function _pathExists(workspace, relPath) {
  const parent = relPath.includes('/')
    ? relPath.replace(/\\/g, '/').replace(/\/[^/]+$/, '')
    : '';
  const name = _basename(relPath);
  try {
    const data = await _fetchList(workspace, parent);
    return (data.dirs || []).some((d) => d.name === name)
      || (data.files || []).some((f) => f.name === name);
  } catch (_) {
    return false;
  }
}

async function _mkdir(workspace, relPath) {
  const res = await fetch(`${API_BASE}/api/workspace/mkdir`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace, path: relPath }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `mkdir failed: ${res.status}`));
  }
  return res.json();
}

async function _writeFile(workspace, relPath, content = '') {
  const res = await fetch(`${API_BASE}/api/workspace/file`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace, path: relPath, content }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_apiErrorDetail(err, `write failed: ${res.status}`));
  }
  return res.json();
}

function _basename(relPath) {
  const parts = String(relPath || '').replace(/\\/g, '/').split('/');
  return parts[parts.length - 1] || relPath;
}

async function _downloadFile(relPath) {
  const root = _wsRoot();
  if (!root) return;
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
  const root = _wsRoot();
  if (!root) return;
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
  const root = _wsRoot();
  if (!root || !fileList?.length) return;
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
  const root = workspace || await _verifiedRoot();
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
  const root = _wsRoot();
  if (!root) return;
  const name = _basename(relPath);
  const recursive = isDir;
  if (!await _confirmDelete(name, { isDir, recursive })) return;
  try {
    await _deletePath(root, relPath, { recursive });
    if (isDir) {
      _docMod()?.removeWorkspaceFileTabsUnder?.(relPath);
      const norm = relPath.replace(/\\/g, '/');
      for (const p of [..._expanded]) {
        if (p === norm || p.startsWith(`${norm}/`)) _expanded.delete(p);
      }
    } else {
      _docMod()?.removeWorkspaceFileTab?.(relPath);
    }
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

async function _loadTree(path = _treePath, { silent = false } = {}) {
  if (_loadTreeInflight?.path === path) return _loadTreeInflight.promise;

  const gen = ++_loadTreeGen;
  const promise = (async () => {
    let root;
    try {
      root = await _verifiedRoot();
    } catch (_) {
      if (gen !== _loadTreeGen) return;
      if (_treeHasContent()) {
        _syncTreeFileTabState();
        return;
      }
      _renderTreeError('No workspace folder selected — open + → Workspace and choose your project folder.');
      return;
    }
    if (!_pane || gen !== _loadTreeGen) return;
    _treePath = path;
    const pathEl = _pane.querySelector('#ws-tree-path');
    if (pathEl) pathEl.textContent = path ? `/${path}` : '';
    const body = _pane.querySelector('#ws-tree-body');
    const showLoading = !silent && _treeBodyIsBlank();
    if (body && showLoading) body.innerHTML = '<div class="ws-explorer-loading">Loading…</div>';
    try {
      const data = await _fetchList(root, path);
      if (gen !== _loadTreeGen || !_pane) return;
      _clearTreeError();
      _renderTree(data);
    } catch (e) {
      if (gen !== _loadTreeGen || !_pane) return;
      if (_treeHasContent()) {
        const banner = _pane.querySelector('#ws-tree-status');
        if (banner) {
          banner.textContent = e.message || 'Could not refresh folder';
          banner.classList.add('ws-tree-status-error');
          banner.style.display = '';
        }
        return;
      }
      _renderTreeError(e.message || 'Unknown error');
    }
  })();

  _loadTreeInflight = { path, promise };
  try {
    await promise;
  } finally {
    if (_loadTreeInflight?.promise === promise) _loadTreeInflight = null;
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
  const root = _wsRoot();
  if (!host || !root) return;
  const { activePath, openPaths } = _fileTabState();
  try {
    const data = await _fetchList(root, dirPath);
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
  const root = _wsRoot();
  if (!root) return;
  try {
    const data = await _fetchFile(root, relPath);
    try {
      _openFileInEditor({
        workspace: root,
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

function _createPickerAnchor() {
  const btn = _pane?.querySelector('#ws-explorer-new');
  return btn?.getBoundingClientRect?.()
    || { left: 0, top: 0, bottom: 0, right: 0, width: 0, height: 0 };
}

function _createNewFile() {
  openWorkspaceSavePicker(_createPickerAnchor(), {
    defaultFilename: 'untitled.txt',
    initialPath: _treePath || '',
    title: 'New file',
    confirmText: 'Create',
    itemType: 'file',
    onSave: async (relPath, workspace) => {
      if (await _pathExists(workspace, relPath)) {
        throw new Error(`"${_basename(relPath)}" already exists — choose another name`);
      }
      await _writeFile(workspace, relPath, '');
      _openFileInEditor({ workspace, path: relPath, content: '' });
      if (uiModule.showToast) uiModule.showToast(`Created ${_basename(relPath)}`);
      _loadTree(_treePath);
    },
  });
}

function _createNewFolder() {
  openWorkspaceSavePicker(_createPickerAnchor(), {
    defaultFilename: 'newfolder',
    initialPath: _treePath || '',
    title: 'New folder',
    confirmText: 'Create',
    itemType: 'folder',
    onSave: async (relPath, workspace) => {
      if (await _pathExists(workspace, relPath)) {
        throw new Error(`"${_basename(relPath)}" already exists — choose another name`);
      }
      await _mkdir(workspace, relPath);
      _expanded.add(relPath);
      if (uiModule.showToast) uiModule.showToast(`Created folder ${_basename(relPath)}`);
      _loadTree(_treePath);
    },
  });
}

let _createMenu = null;
let _createMenuOutside = null;

function _closeCreateMenu() {
  if (_createMenuOutside) {
    document.removeEventListener('mousedown', _createMenuOutside, true);
    _createMenuOutside = null;
  }
  _createMenu?.remove();
  _createMenu = null;
}

function _openCreateMenu() {
  if (!_wsRoot()) {
    if (uiModule.showError) uiModule.showError('Pick a workspace folder first');
    return;
  }
  _closeCreateMenu();
  const btn = _pane?.querySelector('#ws-explorer-new');
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.className = 'ws-explorer-create-menu dropdown';
  menu.innerHTML = `
    <button type="button" class="ws-explorer-create-item" data-kind="file">${_NEW_FILE_ICON}<span>New file</span></button>
    <button type="button" class="ws-explorer-create-item" data-kind="folder">${_NEW_FOLDER_ICON}<span>New folder</span></button>`;
  document.body.appendChild(menu);
  _createMenu = menu;
  menu.style.position = 'fixed';
  menu.style.zIndex = '1200';
  const margin = 8;
  let top = rect.bottom + 4;
  let left = rect.left;
  if (left + menu.offsetWidth > window.innerWidth - margin) {
    left = window.innerWidth - menu.offsetWidth - margin;
  }
  menu.style.left = `${Math.max(margin, left)}px`;
  menu.style.top = `${top}px`;
  menu.querySelector('[data-kind="file"]')?.addEventListener('click', () => {
    _closeCreateMenu();
    _createNewFile();
  });
  menu.querySelector('[data-kind="folder"]')?.addEventListener('click', () => {
    _closeCreateMenu();
    _createNewFolder();
  });
  _createMenuOutside = (e) => {
    if (_createMenu && !_createMenu.contains(e.target) && e.target !== btn) {
      _closeCreateMenu();
    }
  };
  document.addEventListener('mousedown', _createMenuOutside, true);
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
        <div class="ws-mob-hamburger-slot" aria-hidden="true"></div>
        <div class="ws-terminal-dock-title-group">
          <span class="ws-terminal-dock-title">Terminal</span>
          <span class="prometheus-source-label">PROMETHEUS SOURCE</span>
        </div>
      </div>
    </div>
    <div class="ws-terminal-mount" id="ws-mob-terminal-mount"></div>`;
  chat.parentNode.insertBefore(panel, chat);
}

function _syncTerminalDockRef() {
  const td = document.getElementById('ws-terminal-dock');
  if (td?.isConnected) _terminalDock = td;
  else if (_terminalDock && !_terminalDock.isConnected) _terminalDock = null;
}

function _terminalMountEl() {
  if (_isMobileLayout()) {
    _ensureMobileTerminalPanel();
    return document.getElementById('ws-mob-terminal-mount');
  }
  _syncTerminalDockRef();
  return document.getElementById('ws-terminal-mount');
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
      </div>
      <div class="ws-explorer-header-actions">
        <button type="button" class="ws-explorer-btn" id="ws-explorer-new" title="New file or folder">${_NEW_FILE_ICON}</button>
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
  _pane.querySelector('#ws-explorer-new').addEventListener('click', (e) => {
    e.stopPropagation();
    _openCreateMenu();
  });
  _pane.querySelector('#ws-explorer-refresh').addEventListener('click', () => {
    if (_wsRoot()) _loadTree(_treePath);
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
    if (!_wsRoot()) {
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
  const root = _wsRoot();
  if (root) {
    const shown = _wsDisplay() || root;
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

function _workspaceForTerminal() {
  return _wsRoot();
}

function _renderTerminalPlaceholder() {
  const mount = _terminalMountEl();
  if (!mount) return;
  const hint = isDockerWorkspace()
    ? 'Choose a project folder under <code>/workspace</code>. The terminal will appear here once a workspace is selected.'
    : 'Choose a workspace folder first. The terminal will appear here after you pick your project directory.';
  mount.innerHTML = `
    <div class="ws-terminal-idle">
      <div class="ws-terminal-idle-title">Choose a workspace</div>
      <div class="ws-terminal-idle-hint">${hint}</div>
      <button type="button" class="ws-explorer-pick-btn ws-terminal-pick-btn" id="ws-terminal-pick-workspace">Choose folder…</button>
    </div>`;
  mount.querySelector('#ws-terminal-pick-workspace')?.addEventListener('click', () => {
    openWorkspaceBrowser({ fromRoot: true });
  });
}

function _terminalInCurrentMount(mount) {
  if (!mount) return false;
  return !!mount.querySelector('.ws-terminal-tabs');
}

function _prepareWorkspaceTerminal() {
  if (_isMobileLayout()) _ensureMobileTerminalPanel();
  const mount = _terminalMountEl();
  if (!mount) return;
  if (!_workspaceForTerminal()) {
    if (_terminal) _disposeTerminal();
    _renderTerminalPlaceholder();
    return;
  }
  if (!_terminal || !_terminalInCurrentMount(mount)) {
    _mountTerminal();
    return;
  }
  try { _terminal.fitAll?.(); } catch (_) {}
}

function _mountTerminal({ force = false } = {}) {
  const ws = _workspaceForTerminal();
  const mount = _terminalMountEl();
  if (!ws) {
    if (_terminal) _disposeTerminal();
    if (mount) _renderTerminalPlaceholder();
    return;
  }
  if (!mount) return;
  if (!force && _terminal && _terminalInCurrentMount(mount)) {
    try { _terminal.fitAll?.(); } catch (_) {}
    return;
  }
  _disposeTerminal();
  mount.innerHTML = '';
  _terminal = createWorkspaceTerminalPanel(mount, { workspace: ws });
}

function _reconnectTerminal() {
  if (!_isOpen || !_wsRoot()) return;
  if (_terminal?.reconnectAll) _terminal.reconnectAll();
  else _mountTerminal();
}

function _resetEditorWorkbenchLayout(pane) {
  if (!pane) return;
  pane.style.height = '';
  pane.style.maxHeight = '';
  pane.style.minHeight = '';
  pane.style.flex = '';
  pane.style.width = '';
  pane.style.maxWidth = '';
}

function _syncWorkbenchRefsFromDom() {
  const wb = document.getElementById('ws-workbench-column');
  const td = document.getElementById('ws-terminal-dock');
  if (wb?.isConnected) _workbenchCol = wb;
  else if (_workbenchCol && !_workbenchCol.isConnected) _workbenchCol = null;
  if (td?.isConnected) _terminalDock = td;
  else if (_terminalDock && !_terminalDock.isConnected) _terminalDock = null;
}

function _usesGridLayout() {
  return document.body.classList.contains('ws-ide-grid-layout')
    || !!document.getElementById('ws-ide-desktop-grid');
}

function _clearWorkbenchRefs() {
  document.getElementById('ws-workbench-column')?.remove();
  if (_workbenchCol && !_workbenchCol.isConnected) _workbenchCol = null;
  _syncTerminalDockRef();
}

function _adoptEditorIntoWorkbench() {
  if (_isMobileLayout() || _usesGridLayout()) return;
  _syncWorkbenchRefsFromDom();
  const workbench = _workbenchCol || document.getElementById('ws-workbench-column');
  const terminal = _terminalDock || document.getElementById('ws-terminal-dock');
  const pane = document.getElementById('doc-editor-pane');
  if (!workbench || !terminal || !pane) return;
  _resetEditorWorkbenchLayout(pane);
  if (pane.parentNode === workbench) return;
  workbench.insertBefore(pane, terminal);
  document.body.classList.add('doc-view');
}

function _notifyTerminalLayout() {
  requestAnimationFrame(() => {
    _guardWorkbenchTerminalVisible();
    if (!_isMobileLayout()) {
      try { refreshWsPanelLayout(); } catch (_) {}
      try { refreshWsPanelResize(); } catch (_) {}
      try { window.dispatchEvent(new Event('resize')); } catch (_) {}
    }
    try { document.dispatchEvent(new CustomEvent('ws-terminal-layout')); } catch (_) {}
  });
}

/** Desktop: editor height:100% / inline flex can cover the terminal dock (overflow hidden). */
function _guardWorkbenchTerminalVisible() {
  if (_isMobileLayout()) return;
  const container = document.getElementById('ws-ide-desktop-grid')
    || document.getElementById('ws-workbench-column');
  const terminal = document.getElementById('ws-terminal-dock');
  const pane = document.getElementById('doc-editor-pane');
  if (!container || !terminal) return;
  const wb = container.getBoundingClientRect();
  const td = terminal.getBoundingClientRect();
  const clipped = td.height < 80 || td.bottom > wb.bottom + 2 || td.top >= wb.bottom - 4;
  if (!clipped) return;
  _resetEditorWorkbenchLayout(pane);
  try { refreshWsPanelResize(); } catch (_) {}
}

function _ensureWorkbenchColumn() {
  if (_isMobileLayout()) return true;
  const chat = document.getElementById('chat-container');
  if (!chat) return false;
  if (_usesGridLayout()) {
    mountWsPanelLayout();
    _notifyTerminalLayout();
    return true;
  }
  _syncWorkbenchRefsFromDom();
  _syncTerminalDockRef();
  if (!_workbenchCol && !document.getElementById('ws-terminal-dock')) {
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
  if (!_workbenchCol) {
    _workbenchCol = document.createElement('div');
    _workbenchCol.id = 'ws-workbench-column';
    _workbenchCol.className = 'ws-workbench-column';
    if (_terminalDock && !_terminalDock.parentNode) {
      _workbenchCol.appendChild(_terminalDock);
    }
  }
  if (!_workbenchCol.parentNode) {
    chat.parentNode.insertBefore(_workbenchCol, chat);
  }
  _adoptEditorIntoWorkbench();
  mountWsPanelLayout();
  _notifyTerminalLayout();
  return true;
}

function _releaseWorkbench() {
  unmountWsPanelLayout();
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
  _syncTerminalDockRef();
}

function _ensureEditorInWorkbench() {
  if (_isMobileLayout()) return;
  const docMod = _docMod();
  if (_usesGridLayout()) {
    if (docMod) {
      docMod.ensurePaneMounted?.();
      if (!docMod.isPanelOpen?.()) docMod.openPanel?.();
    }
    mountWsPanelLayout();
    _notifyTerminalLayout();
    return;
  }
  _ensureWorkbenchColumn();
  if (!docMod) return;
  docMod.ensurePaneMounted?.();
  if (!docMod.isPanelOpen?.()) {
    docMod.openPanel?.();
  }
  _adoptEditorIntoWorkbench();
  _notifyTerminalLayout();
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
  return isMobileIdeLayout();
}

function _onIdeLayoutModeChange() {
  if (!_isOpen) return;
  if (_isMobileLayout()) {
    unmountWsPanelLayout();
    unmountWsPanelResize();
    _releaseWorkbench();
    _ensureMobileTerminalPanel();
  } else {
    _ensureWorkbenchColumn();
    mountWsPanelLayout();
  }
  _prepareWorkspaceTerminal();
  _notifyTerminalLayout();
}

let _lastMobileLayout = null;
function _watchIdeLayoutMode() {
  if (_watchIdeLayoutMode._wired) return;
  _watchIdeLayoutMode._wired = true;
  _lastMobileLayout = _isMobileLayout();
  document.addEventListener(IDE_LAYOUT_EVENT, () => {
    const now = _isMobileLayout();
    if (now === _lastMobileLayout) return;
    _lastMobileLayout = now;
    _onIdeLayoutModeChange();
    restoreWorkspaceIde().catch(() => {});
  });
}

async function _resolveWorkspacePath(ws) {
  return ws || await _verifiedRoot();
}

/** Reload file tree + terminal from the verified workspace store (same path chat uses). */
async function _syncExplorerToWorkspace({ clearEditorTabs = false } = {}) {
  const verified = await ensureVerifiedWorkspace();
  if (!verified?.path) {
    _lastSyncedRoot = '';
    _treePath = '';
    _expanded = new Set(['']);
    if (clearEditorTabs) _docMod()?.clearWorkspaceFiles?.();
    _syncWorkspaceLabel();
    _renderNoWorkspaceState();
    _mountTerminal();
    return false;
  }

  const rootChanged = _lastSyncedRoot !== verified.path;
  _lastSyncedRoot = verified.path;
  if (rootChanged) {
    _treePath = '';
    _expanded = new Set(['']);
    const body = _treeBodyEl();
    if (body) body.innerHTML = '';
  }
  if (clearEditorTabs || rootChanged) _docMod()?.clearWorkspaceFiles?.();
  _syncWorkspaceLabel();
  if (rootChanged) _mountTerminal({ force: true });
  else _prepareWorkspaceTerminal();
  await _loadTree(_treePath, { silent: !rootChanged });
  _notifyTerminalLayout();
  return true;
}

export async function openPanel({ promptWorkspace = false } = {}) {
  if (_isOpen) {
    await _syncExplorerToWorkspace();
    _notifyTerminalLayout();
    return;
  }

  if (!_mountPane()) return;

  _watchIdeLayoutMode();

  document.body.classList.add('ws-explorer-view');
  _isOpen = true;
  Storage.set(STORAGE_OPEN, '1');

  const overflow = document.getElementById('overflow-ws-files-btn');
  if (overflow) overflow.classList.add('active');
  if (_isMobileLayout()) {
    _ensureMobileTerminalPanel();
  } else {
    _ensureEditorInWorkbench();
    mountWsPanelResize();
  }

  const ok = await _syncExplorerToWorkspace();
  if (!ok && promptWorkspace) {
    openWorkspaceBrowser({ fromRoot: true });
    if (uiModule.showToast) uiModule.showToast('Select a workspace folder');
  }
}

export function closePanel() {
  if (!_isOpen) return;
  _closeCreateMenu();
  if (_docMod()?.hasDirtyWorkspaceFiles?.() && !confirm('Discard unsaved file changes?')) return;
  unmountWsPanelResize();
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
    if (_isOpen) await _syncExplorerToWorkspace({ clearEditorTabs: true });
    return;
  }
  if (!_isOpen) {
    await openPanel();
    return;
  }
  await _syncExplorerToWorkspace({ clearEditorTabs: !resync });
}

function _onWorkspaceFileTabsChanged() {
  if (!_isOpen || !_wsRoot()) return;
  _syncTreeFileTabState();
}

/** Restore file tree + terminal from verified workspace store (survives browser refresh). */
async function _restoreWorkspaceIdeImpl() {
  await whenWorkspaceReady();
  const verified = await ensureVerifiedWorkspace();
  const ws = verified?.path || '';

  if (ws) {
    if (!_isOpen) await openPanel();
    else await _syncExplorerToWorkspace();
  } else if (_isMobileLayout()) {
    return;
  } else if (!_isOpen) {
    await openPanel();
  } else {
    await _syncExplorerToWorkspace();
  }

  if (_isMobileLayout()) {
    _ensureMobileTerminalPanel();
    _prepareWorkspaceTerminal();
    _notifyTerminalLayout();
    return;
  }

  _ensureWorkbenchColumn();
  _ensureEditorInWorkbench();
  mountWsPanelLayout();
  _docMod()?.openPanel?.();
  _docMod()?.ensurePaneMounted?.();
  _adoptEditorIntoWorkbench();
  _clearDesktopPanelInlineStyles();
  _notifyTerminalLayout();
}

export async function restoreWorkspaceIde() {
  if (_restoreIdeInflight) return _restoreIdeInflight;
  _restoreIdeInflight = _restoreWorkspaceIdeImpl().finally(() => {
    if (_restoreIdeInflight) _restoreIdeInflight = null;
  });
  return _restoreIdeInflight;
}

function _clearDesktopPanelInlineStyles() {
  if (_isMobileLayout()) return;
  document.body.classList.remove('ws-mob-view');
  delete document.body.dataset.wsMobLeft;
  document.body.style.removeProperty('height');
  document.querySelectorAll('.ws-mob-panel-empty').forEach((el) => el.remove());
  const props = ['display', 'width', 'max-width', 'min-width', 'flex', 'height', 'max-height', 'overflow', 'position', 'visibility'];
  ['ws-explorer-pane', 'ws-workbench-column', 'doc-editor-pane', 'ws-terminal-dock', 'chat-container'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    props.forEach((p) => el.style.removeProperty(p));
  });
  document.getElementById('doc-divider')?.style.removeProperty('display');
}

/** Desktop IDE layout: project files + editor + bottom terminal. Restored on load. */
export async function ensureIdeLayoutOpen() {
  if (Storage.get(STORAGE_OPEN, '') === '0') Storage.remove(STORAGE_OPEN);
  await restoreWorkspaceIde();
}

export function initWorkspaceExplorer() {
  _watchIdeLayoutMode();
  document.addEventListener('ws-adopt-editor-workbench', () => {
    if (!_isMobileLayout()) _ensureEditorInWorkbench();
  });
  document.addEventListener('ws-panel-layout-mounted', _clearWorkbenchRefs);
  document.addEventListener('workspace-environment-ready', () => {
    restoreWorkspaceIde().catch(() => {});
  });
  document.addEventListener('workspace-changed', _onWorkspaceChanged);
  document.addEventListener('workspace-file-tabs-changed', _onWorkspaceFileTabsChanged);
  document.addEventListener('workspace-file-saved', (e) => {
    if (!_isOpen || !_wsRoot()) return;
    const savedPath = e.detail?.path || '';
    const visible = savedPath && _pane?.querySelector(`#ws-tree-body .ws-tree-row[data-path="${CSS.escape(savedPath)}"]`);
    if (visible) {
      _syncTreeFileTabState();
      return;
    }
    _loadTree(_treePath, { silent: true });
  });
  document.addEventListener('open-workspace-explorer', () => {
    openPanel().catch(() => {});
  });
  document.addEventListener('prepare-workspace-terminal', () => {
    _prepareWorkspaceTerminal();
  });
  document.addEventListener('ws-mob-terminal-show', () => {
    _prepareWorkspaceTerminal();
    _notifyTerminalLayout();
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
  restoreWorkspaceIde,
};
