// static/js/wsEditorSave.js — Save As picker: choose folder + filename inside the workspace.

import uiModule from './ui.js';
import { ensureVerifiedWorkspace, whenWorkspaceReady } from './workspace.js';

const API_BASE = window.location.origin;

let _menu = null;
let _outsideHandler = null;
let _escHandler = null;

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s) : String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function _fetchList(workspace, relPath = '') {
  const qs = new URLSearchParams({ workspace, path: relPath || '' });
  const res = await fetch(`${API_BASE}/api/workspace/list?${qs}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err.detail === 'string' ? err.detail : `list failed: ${res.status}`;
    throw new Error(detail);
  }
  return res.json();
}

function _closeMenu() {
  if (_outsideHandler) {
    document.removeEventListener('mousedown', _outsideHandler, true);
    _outsideHandler = null;
  }
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler, true);
    _escHandler = null;
  }
  _menu?.remove();
  _menu = null;
}

function _positionMenu(menu, anchorRect) {
  menu.style.position = 'fixed';
  menu.style.zIndex = '1200';
  menu.style.display = 'block';
  const margin = 8;
  let left = anchorRect.left;
  let top = anchorRect.top - menu.offsetHeight - 6;
  if (top < margin) top = anchorRect.bottom + 6;
  if (left + menu.offsetWidth > window.innerWidth - margin) {
    left = window.innerWidth - menu.offsetWidth - margin;
  }
  if (left < margin) left = margin;
  if (top + menu.offsetHeight > window.innerHeight - margin) {
    top = Math.max(margin, window.innerHeight - menu.offsetHeight - margin);
  }
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

/**
 * Open a workspace Save As picker anchored to a button.
 * @param {DOMRect} anchorRect
 * @param {{ defaultFilename?: string, onSave: (relPath: string, workspaceRoot: string) => Promise<void>|void }} opts
 */
export async function openWorkspaceSavePicker(anchorRect, {
  defaultFilename = 'untitled.txt',
  onSave,
  title = 'Save to workspace',
  confirmText = 'Save here',
  initialPath = '',
} = {}) {
  _closeMenu();
  await whenWorkspaceReady();
  const verified = await ensureVerifiedWorkspace();
  const workspace = verified?.path || '';
  if (!workspace) {
    if (uiModule?.showToast) uiModule.showToast('Select a workspace folder first (+ → Workspace)');
    return;
  }

  const menu = document.createElement('div');
  menu.id = 'doc-save-menu';
  menu.className = 'doc-save-menu dropdown';
  menu.innerHTML = `
    <div class="doc-save-menu-header">
      <span class="doc-save-menu-title"></span>
      <button type="button" class="doc-save-menu-close" title="Close">&times;</button>
    </div>
    <div class="doc-save-menu-path"></div>
    <div class="doc-save-menu-list"></div>
    <div class="doc-save-menu-footer">
      <input type="text" class="doc-save-menu-filename" placeholder="filename" autocomplete="off" />
      <button type="button" class="doc-save-menu-confirm memory-toolbar-btn active"></button>
    </div>`;
  document.body.appendChild(menu);
  _menu = menu;

  const pathEl = menu.querySelector('.doc-save-menu-path');
  const listEl = menu.querySelector('.doc-save-menu-list');
  const nameInput = menu.querySelector('.doc-save-menu-filename');
  const titleEl = menu.querySelector('.doc-save-menu-title');
  const confirmBtn = menu.querySelector('.doc-save-menu-confirm');
  titleEl.textContent = title;
  confirmBtn.textContent = confirmText;
  nameInput.value = defaultFilename || 'untitled.txt';

  let curPath = (initialPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');

  async function renderFolder() {
    listEl.innerHTML = '<div class="doc-save-menu-loading">Loading…</div>';
    pathEl.textContent = curPath ? `/${curPath}` : '/';
    try {
      const data = await _fetchList(workspace, curPath);
      let html = '';
      if (data.parent !== null && data.parent !== undefined) {
        html += `<button type="button" class="doc-save-menu-row doc-save-menu-up" data-path="${_esc(data.parent)}">↑ Parent folder</button>`;
      }
      for (const d of data.dirs || []) {
        html += `<button type="button" class="doc-save-menu-row doc-save-menu-dir" data-path="${_esc(d.path)}">${_esc(d.name)}/</button>`;
      }
      if (!html) {
        html = '<div class="doc-save-menu-empty">No subfolders — save file in this folder below.</div>';
      }
      listEl.innerHTML = html;
      listEl.querySelector('.doc-save-menu-up')?.addEventListener('click', (e) => {
        curPath = e.currentTarget.dataset.path || '';
        renderFolder();
      });
      listEl.querySelectorAll('.doc-save-menu-dir').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          curPath = e.currentTarget.dataset.path || '';
          renderFolder();
        });
      });
    } catch (e) {
      listEl.innerHTML = `<div class="doc-save-menu-error">${_esc(e.message || 'Could not list folder')}</div>`;
    }
  }

  async function commitSave() {
    const name = (nameInput.value || '').trim().replace(/\\/g, '/').split('/').pop();
    if (!name || name === '.' || name === '..') {
      if (uiModule?.showError) uiModule.showError('Enter a file name');
      nameInput.focus();
      return;
    }
    const rel = curPath ? `${curPath.replace(/\/+$/, '')}/${name}` : name;
    try {
      await onSave(rel, workspace);
      _closeMenu();
    } catch (e) {
      if (uiModule?.showError) uiModule.showError(e.message || 'Save failed');
    }
  }

  menu.querySelector('.doc-save-menu-close')?.addEventListener('click', _closeMenu);
  menu.querySelector('.doc-save-menu-confirm')?.addEventListener('click', commitSave);
  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commitSave(); }
    if (e.key === 'Escape') { e.preventDefault(); _closeMenu(); }
  });

  _outsideHandler = (e) => {
    if (_menu && !_menu.contains(e.target) && !e.target.closest('#doc-save-split')) {
      _closeMenu();
    }
  };
  _escHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); _closeMenu(); }
  };
  document.addEventListener('mousedown', _outsideHandler, true);
  document.addEventListener('keydown', _escHandler, true);

  await renderFolder();
  requestAnimationFrame(() => {
    _positionMenu(menu, anchorRect);
    nameInput.focus();
    nameInput.select();
  });
}

export function closeWorkspaceSavePicker() {
  _closeMenu();
}
