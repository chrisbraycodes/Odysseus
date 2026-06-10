// static/js/workspace.js
//
// Workspace picker: browse server directories in a draggable modal, choose a
// folder, and show it as a removable pill in the chat input bar. While set, the
// chat request sends `workspace` so the agent's file/shell tools are confined
// to that folder (see routes/chat_routes.py + src/tool_execution.py).

import Storage, { KEYS } from './storage.js';
import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
// Same folder glyph as the overflow menu item + pill (not an emoji).
const _FOLDER_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
let _modal = null;
let _curPath = '';
let _dockerWorkspace = false;
let _readyPromise = null;
let _defaultRoot = '';

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s) : String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function getWorkspace() {
  return Storage.get(KEYS.WORKSPACE, '') || '';
}

function _basename(p) {
  if (!p) return '';
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

export function isDockerWorkspace() {
  return _dockerWorkspace;
}

export function whenWorkspaceReady() {
  return _readyPromise || Promise.resolve();
}

export function syncWorkspaceIndicator(path, { displayPath = '' } = {}) {
  const shown = displayPath || path;
  const pill = document.getElementById('workspace-indicator-btn');
  const name = document.getElementById('workspace-indicator-name');
  const overflow = document.getElementById('overflow-workspace-btn');
  const filesBtn = document.getElementById('workspace-files-btn');
  if (pill) {
    pill.style.display = path ? '' : 'none';
    pill.classList.toggle('active', !!path);
    if (path) pill.title = `Workspace: ${shown} — click name to open files, ✕ to clear`;
  }
  if (name) name.textContent = path ? _basename(shown) : '';
  if (overflow) overflow.classList.toggle('active', !!path);
  if (filesBtn) filesBtn.style.display = path ? '' : 'none';
  try { document.dispatchEvent(new CustomEvent('overflow-state-change')); } catch (_) {}
}

export function setWorkspace(path, { displayPath = '', notify = true } = {}) {
  const prev = getWorkspace();
  if (path) Storage.set(KEYS.WORKSPACE, path);
  else Storage.remove(KEYS.WORKSPACE);
  syncWorkspaceIndicator(path || '', { displayPath });
  const shown = displayPath || path || '';
  const changed = (prev || '') !== (path || '');
  if (notify) {
    try {
      document.dispatchEvent(new CustomEvent('workspace-changed', {
        detail: {
          path: path || '',
          displayPath: shown,
          previous: prev,
          resync: !changed,
        },
      }));
    } catch (_) {}
  }
}

/** Resolve a stored or typed path against the server (maps Desktop → /workspace in Docker). */
export async function normalizeWorkspace(path, { notify = false } = {}) {
  if (!path) return { valid: false, path: '', displayPath: '' };
  const v = await validateWorkspace(path);
  _dockerWorkspace = !!v.docker_workspace;
  if (v.default_root) _defaultRoot = v.default_root;
  if (v.valid && v.path) {
    const displayPath = v.display_path || v.path;
    const prev = getWorkspace();
    const shouldNotify = notify || prev !== v.path;
    setWorkspace(v.path, { displayPath, notify: shouldNotify });
    return {
      valid: true,
      path: v.path,
      displayPath,
      normalizedFrom: v.normalized_from || null,
    };
  }
  if (_dockerWorkspace) clearWorkspace();
  return { valid: false, path: '', displayPath: '' };
}

export function clearWorkspace() {
  setWorkspace('');
  if (uiModule && uiModule.showToast) uiModule.showToast('Workspace cleared');
}

async function _probeEnvironment() {
  try {
    const probe = await validateWorkspace('');
    _dockerWorkspace = !!probe.docker_workspace;
    if (probe.default_root) _defaultRoot = probe.default_root;
    return probe;
  } catch (_) {
    return { docker_workspace: false, default_root: '' };
  }
}

async function _load(path) {
  const url = `${API_BASE}/api/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) {
    let detail = `browse failed: ${res.status}`;
    try {
      const err = await res.json();
      if (typeof err.detail === 'string') detail = err.detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function _shownPath(data) {
  return data?.display_path || data?.path || '';
}

function _setBodyLoading(msg = 'Loading folders…') {
  const body = _modal?.querySelector('#workspace-body');
  if (body) body.innerHTML = `<div class="workspace-empty">${_esc(msg)}</div>`;
}

function _updateDockerHint(inDocker) {
  const hint = _modal?.querySelector('#workspace-docker-hint');
  if (!hint) return;
  hint.style.display = inDocker ? '' : 'none';
}

function _render(data) {
  _dockerWorkspace = !!data.docker_workspace;
  if (data.default_root) _defaultRoot = data.default_root;
  _curPath = data.path;
  const body = _modal.querySelector('#workspace-body');
  const pathEl = _modal.querySelector('#workspace-cur-path');
  const useBtn = _modal.querySelector('#workspace-use');
  const shown = _shownPath(data);
  if (pathEl) {
    pathEl.value = shown;
    pathEl.title = shown;
    pathEl.placeholder = _dockerWorkspace
      ? 'Container path under /workspace — press Enter to go'
      : 'Type or paste a folder path, then press Enter';
  }
  if (useBtn) {
    useBtn.textContent = data.dirs?.length
      ? `Use "${_basename(shown)}" as workspace`
      : 'Use this folder as workspace';
  }
  _updateDockerHint(_dockerWorkspace);
  let rows = '';
  if (data.parent) {
    const parentShown = data.parent_display || data.parent;
    rows += `<div class="workspace-row workspace-up" data-path="${encodeURIComponent(data.parent)}" title="Up to ${_esc(parentShown)}">↑ ${_esc(_basename(parentShown) || '..')}</div>`;
  }
  for (const d of data.dirs) {
    const label = d.display_path || d.path;
    rows += `<div class="workspace-row" data-path="${encodeURIComponent(d.path)}" title="${_esc(label)}">${_FOLDER_SVG}<span>${_esc(d.name)}</span></div>`;
  }
  if (!data.dirs.length && !data.parent) {
    rows = `<div class="workspace-empty">
      <div class="workspace-empty-title">No subfolders here</div>
      <div class="workspace-empty-hint">Click <strong>Use this folder</strong> below to work in <code>${_esc(shown)}</code>, or import a folder from your computer.</div>
    </div>`;
  } else if (!data.dirs.length) {
    rows += `<div class="workspace-empty workspace-empty-inline">No subfolders — use the button below to select <code>${_esc(shown)}</code></div>`;
  }
  body.innerHTML = rows;
  body.querySelectorAll('.workspace-row').forEach((row) => {
    row.addEventListener('click', () => _navigate(decodeURIComponent(row.dataset.path)));
  });
}

async function _navigate(path) {
  _setBodyLoading();
  try {
    _render(await _load(path));
  } catch (e) {
    if (uiModule?.showError) uiModule.showError(e.message || 'Could not open folder');
    _setBodyLoading('Could not open that folder — check the path and try again.');
  }
}

async function _importFiles(fileList) {
  const base = _curPath;
  if (!base || !fileList?.length) {
    if (uiModule?.showError) uiModule.showError('Open a folder in the picker first');
    return;
  }
  let ok = 0;
  for (const file of fileList) {
    const rel = (file.webkitRelativePath || file.name).replace(/\\/g, '/');
    const slash = rel.lastIndexOf('/');
    const subDir = slash >= 0 ? rel.slice(0, slash) : '';
    const fd = new FormData();
    fd.append('workspace', base);
    if (subDir) fd.append('path', subDir);
    fd.append('file', file, file.name);
    const res = await fetch(`${API_BASE}/api/workspace/import`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
    });
    if (!res.ok) {
      let detail = `import failed: ${res.status}`;
      try {
        const err = await res.json();
        if (typeof err.detail === 'string') detail = err.detail;
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    ok += 1;
  }
  if (uiModule?.showToast) {
    uiModule.showToast(ok === 1 ? `Imported ${fileList[0].name}` : `Imported ${ok} files`);
  }
  _render(await _load(base));
}

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'workspace-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content workspace-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Select workspace</h4>
        <button class="close-btn" id="workspace-close" aria-label="Close">✖</button>
      </div>
      <div class="workspace-docker-hint" id="workspace-docker-hint" style="display:none">
        Your Desktop is mounted at <code>/workspace</code>. Pick a folder here, or import one from your computer. Host paths like <code>C:\\...</code> are not visible inside the container.
      </div>
      <input type="text" class="styled-prompt-input workspace-cur" id="workspace-cur-path"
             spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off"
             placeholder="Type or paste a folder path, then press Enter" />
      <div class="workspace-toolbar">
        <button type="button" class="workspace-tool-btn" id="workspace-import-folder" title="Copy a folder from your computer into the current location">Import folder…</button>
        <button type="button" class="workspace-tool-btn" id="workspace-import-files" title="Copy files from your computer into the current location">Import files…</button>
        <button type="button" class="workspace-tool-btn workspace-tool-btn-muted" id="workspace-go-root" title="Jump to workspace root">Desktop root</button>
      </div>
      <input type="file" id="workspace-folder-input" webkitdirectory directory multiple hidden />
      <input type="file" id="workspace-files-input" multiple hidden />
      <div class="modal-body workspace-body" id="workspace-body"></div>
      <div class="modal-footer workspace-footer">
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-cancel">Cancel</button>
        <button type="button" class="confirm-btn confirm-btn-primary" id="workspace-use">Use this folder</button>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#workspace-close').addEventListener('click', closeWorkspaceBrowser);
  _modal.querySelector('#workspace-cancel').addEventListener('click', closeWorkspaceBrowser);
  _modal.querySelector('#workspace-cur-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = e.target.value.trim();
      if (v) _navigate(v);
    }
  });
  _modal.querySelector('#workspace-use').addEventListener('click', async () => {
    try {
      const n = await normalizeWorkspace(_curPath, { notify: true });
      if (!n.valid) {
        if (uiModule?.showError) {
          uiModule.showError(_dockerWorkspace
            ? 'Pick a folder under /workspace (your Desktop mount)'
            : 'That folder is not available');
        }
        return;
      }
      if (uiModule?.showToast) uiModule.showToast(`Workspace set: ${_basename(n.displayPath || n.path)}`);
      closeWorkspaceBrowser();
    } catch (_) {
      if (uiModule?.showError) uiModule.showError('Could not set workspace');
    }
  });
  _modal.querySelector('#workspace-import-folder').addEventListener('click', () => {
    if (!_curPath) {
      if (uiModule?.showError) uiModule.showError('Wait for the folder list to load first');
      return;
    }
    _modal.querySelector('#workspace-folder-input')?.click();
  });
  _modal.querySelector('#workspace-import-files').addEventListener('click', () => {
    if (!_curPath) {
      if (uiModule?.showError) uiModule.showError('Wait for the folder list to load first');
      return;
    }
    _modal.querySelector('#workspace-files-input')?.click();
  });
  _modal.querySelector('#workspace-folder-input')?.addEventListener('change', (e) => {
    const files = e.target.files;
    if (files?.length) {
      _importFiles(files).catch((err) => {
        if (uiModule?.showError) uiModule.showError(err.message || 'Import failed');
      });
    }
    e.target.value = '';
  });
  _modal.querySelector('#workspace-files-input')?.addEventListener('change', (e) => {
    const files = e.target.files;
    if (files?.length) {
      _importFiles(files).catch((err) => {
        if (uiModule?.showError) uiModule.showError(err.message || 'Import failed');
      });
    }
    e.target.value = '';
  });
  _modal.querySelector('#workspace-go-root').addEventListener('click', () => {
    const root = _defaultRoot || (_dockerWorkspace ? '/workspace' : '');
    if (root) _navigate(root);
  });
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

async function _browseStartPath({ fromRoot = false } = {}) {
  await whenWorkspaceReady();
  const probe = await _probeEnvironment();
  if (probe.docker_workspace) {
    return probe.default_root || '/workspace';
  }
  if (fromRoot) return '';
  const stored = getWorkspace();
  return stored || '';
}

export async function openWorkspaceBrowser({ fromRoot = true } = {}) {
  const modal = _getModal();
  modal.style.display = 'flex';
  _setBodyLoading();
  const startPath = await _browseStartPath({ fromRoot });
  try {
    _render(await _load(startPath));
  } catch (e) {
    if (_dockerWorkspace || _defaultRoot) {
      try {
        _render(await _load(_defaultRoot || '/workspace'));
        return;
      } catch (_) { /* fall through */ }
    }
    _setBodyLoading(e.message || 'Could not browse folders');
    if (uiModule?.showError) uiModule.showError(e.message || 'Could not browse folders');
  }
}

export function closeWorkspaceBrowser() {
  if (_modal) _modal.style.display = 'none';
}

export async function validateWorkspace(path) {
  const url = `${API_BASE}/api/workspace/validate${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`validate failed: ${res.status}`);
  return res.json();
}

/** True when agent shell/file work may proceed; opens picker if needed. */
export async function ensureWorkspaceReady({ requireDocker = true } = {}) {
  await whenWorkspaceReady();
  const stored = getWorkspace();
  if (stored) {
    try {
      const n = await normalizeWorkspace(stored);
      if (n.valid) return true;
      clearWorkspace();
      if (uiModule && uiModule.showToast) {
        uiModule.showToast('Previous workspace folder not found — pick again');
      }
    } catch (_) {
      /* fall through to picker */
    }
  }
  let docker = requireDocker;
  try {
    const probe = await validateWorkspace('');
    docker = !!probe.docker_workspace;
  } catch (_) {
    docker = false;
  }
  if (!docker) return true;
  await openWorkspaceBrowser({ fromRoot: true });
  if (uiModule && uiModule.showToast) {
    uiModule.showToast('Select a workspace folder under /workspace');
  }
  return false;
}

async function _bootstrapWorkspace() {
  const probe = await _probeEnvironment();
  const stored = getWorkspace();
  if (stored) {
    try {
      await normalizeWorkspace(stored);
    } catch (_) {
      clearWorkspace();
    }
  } else if (_dockerWorkspace) {
    const root = probe.default_root || '/workspace';
    try {
      await normalizeWorkspace(root);
    } catch (_) {
      syncWorkspaceIndicator('');
    }
  } else {
    syncWorkspaceIndicator('');
  }
  try {
    document.dispatchEvent(new CustomEvent('workspace-environment-ready', {
      detail: { docker: _dockerWorkspace },
    }));
  } catch (_) {}
}

export function initWorkspace() {
  if (!_readyPromise) _readyPromise = _bootstrapWorkspace();
  syncWorkspaceIndicator(getWorkspace());
  const overflow = document.getElementById('overflow-workspace-btn');
  if (overflow) overflow.addEventListener('click', () => openWorkspaceBrowser({ fromRoot: true }));
  const pill = document.getElementById('workspace-indicator-btn');
  if (pill) {
    pill.addEventListener('click', (e) => {
      if (e.target.closest('.tool-indicator-x')) {
        clearWorkspace();
        return;
      }
      try {
        document.dispatchEvent(new CustomEvent('open-workspace-explorer'));
      } catch (_) {}
    });
  }
  const filesBtn = document.getElementById('workspace-files-btn');
  if (filesBtn) {
    filesBtn.addEventListener('click', () => {
      try {
        document.dispatchEvent(new CustomEvent('open-workspace-explorer'));
      } catch (_) {}
    });
  }
}

export default {
  initWorkspace,
  openWorkspaceBrowser,
  getWorkspace,
  setWorkspace,
  clearWorkspace,
  syncWorkspaceIndicator,
  validateWorkspace,
  normalizeWorkspace,
  ensureWorkspaceReady,
  whenWorkspaceReady,
  isDockerWorkspace,
};
