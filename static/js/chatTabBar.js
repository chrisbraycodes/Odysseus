/**
 * Multi-tab chat bar — each open chat gets a tab with a close (×) button.
 * Closing a tab stops any in-flight work for that session and deletes it.
 */
import uiModule from './ui.js';

const API_BASE = '';

/** @type {{ id: string, sessionId: string|null, pending: object|null, title: string }[]} */
let _tabs = [];
let _activeTabId = null;
let _pendingCounter = 0;
let _switching = false;

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s) : String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _barEl() {
  return document.getElementById('chat-tab-bar');
}

function _syncPendingToSessions(tab) {
  const sm = window.sessionModule;
  if (!sm?.setPendingChat) return;
  sm.setPendingChat(tab?.pending || null);
}

/** Save the active tab's pending-chat payload before switching away. */
function _saveActiveTabState() {
  const tab = _tabs.find(t => t.id === _activeTabId);
  if (!tab) return;
  const sm = window.sessionModule;
  if (sm?.getPendingChat) {
    tab.pending = sm.getPendingChat() || null;
  }
}

function _render() {
  const bar = _barEl();
  if (!bar) return;

  const meta = document.getElementById('current-meta');

  if (_tabs.length < 2) {
    bar.innerHTML = '';
    bar.style.display = 'none';
    if (meta) meta.style.visibility = '';
    return;
  }

  bar.style.display = 'flex';
  if (meta) meta.style.visibility = 'hidden';

  let html = '<div class="chat-tab-scroll" id="chat-tab-scroll">';
  for (const tab of _tabs) {
    const isActive = tab.id === _activeTabId;
    const title = tab.title || 'New Chat';
    html += `<div class="chat-tab${isActive ? ' active' : ''}" data-chat-tab-id="${_esc(tab.id)}" title="${_esc(title)}">
      <span class="chat-tab-title">${_esc(title)}</span>
      <button type="button" class="chat-tab-close" data-chat-tab-id="${_esc(tab.id)}" title="Close chat">&times;</button>
    </div>`;
  }
  html += '</div>';
  bar.innerHTML = html;
}

async function _showPendingTabUI(tab) {
  const sm = window.sessionModule;
  const chat = window.chatModule;
  if (!sm || !tab) return;

  sm.setCurrentSessionId(null);
  _syncPendingToSessions(tab);

  document.querySelectorAll('.list-item.active-session, .session-item.active').forEach(el => {
    el.classList.remove('active-session', 'active');
  });

  if (window.documentModule?.isPanelOpen?.() && window.documentModule.closePanel) {
    window.documentModule.closePanel();
  }

  const box = document.getElementById('chat-history');
  if (box) box.innerHTML = '';
  if (chat?.showWelcomeScreen) chat.showWelcomeScreen();

  if (sm.updateModelPicker) sm.updateModelPicker();

  const metaEl = document.getElementById('current-meta');
  if (metaEl) metaEl.textContent = tab.title || 'New Chat';

  const msgInput = document.getElementById('message');
  if (msgInput) {
    msgInput.disabled = false;
    msgInput.value = '';
    if (window.innerWidth > 768) msgInput.focus();
  }

  if (window._updateSendBtnIcon) window._updateSendBtnIcon();
}

async function _activateTab(tabId) {
  if (_switching) return;
  const tab = _tabs.find(t => t.id === tabId);
  if (!tab || tabId === _activeTabId) return;

  _switching = true;
  try {
    _saveActiveTabState();

    const sm = window.sessionModule;
    const prevSessionId = sm?.getCurrentSessionId?.() || null;
    if (window.chatModule?.detachCurrentStream) {
      window.chatModule.detachCurrentStream(prevSessionId);
    } else if (window.chatModule?.abortCurrentRequest) {
      window.chatModule.abortCurrentRequest();
    }

    _activeTabId = tabId;
    _render();

    if (tab.sessionId) {
      _syncPendingToSessions({ pending: null });
      if (sm?.selectSession) {
        await sm.selectSession(tab.sessionId, { fromTabBar: true, keepSidebar: true });
      }
    } else {
      await _showPendingTabUI(tab);
    }
  } finally {
    _switching = false;
  }
}

function _findTabIndex(tabId) {
  return _tabs.findIndex(t => t.id === tabId);
}

async function _activateAdjacentTab(closedIndex) {
  if (_tabs.length === 0) {
    _activeTabId = null;
    _render();
    const sm = window.sessionModule;
    const chat = window.chatModule;
    if (sm) sm.setCurrentSessionId(null);
    if (sm?.setPendingChat) sm.setPendingChat(null);
    const box = document.getElementById('chat-history');
    if (box) box.innerHTML = '';
    if (chat?.showWelcomeScreen) chat.showWelcomeScreen();
    const metaEl = document.getElementById('current-meta');
    if (metaEl) metaEl.textContent = 'Odysseus Chat';
    return;
  }
  const next = _tabs[Math.min(closedIndex, _tabs.length - 1)];
  await _activateTab(next.id);
}

async function _stopAndDeleteSession(sessionId) {
  if (!sessionId) return;
  if (window.chatModule?.abortSessionStream) {
    window.chatModule.abortSessionStream(sessionId, true);
  }
  try {
    const pm = await import('./presets.js');
    if (pm.removePersistentChat) pm.removePersistentChat(sessionId);
  } catch (_) {}
  try {
    await fetch(`${API_BASE}/api/session/${sessionId}`, { method: 'DELETE' });
  } catch (_) {}
}

export function init() {
  const bar = _barEl();
  if (!bar || bar.dataset.bound) return;
  bar.dataset.bound = '1';

  bar.addEventListener('click', async (e) => {
    const closeBtn = e.target.closest('.chat-tab-close');
    if (closeBtn) {
      e.stopPropagation();
      await closeTab(closeBtn.dataset.chatTabId);
      return;
    }
    const tabEl = e.target.closest('.chat-tab');
    if (tabEl && !e.target.closest('.chat-tab-close')) {
      await _activateTab(tabEl.dataset.chatTabId);
    }
  });
}

/** Open a new pending (unsaved) chat tab. */
export function openNewPendingTab(pending, title = 'New Chat') {
  _saveActiveTabState();

  const prevSessionId = window.sessionModule?.getCurrentSessionId?.() || null;
  if (window.chatModule?.detachCurrentStream) {
    window.chatModule.detachCurrentStream(prevSessionId);
  }

  if (window.groupModule?.isActive?.() && window.groupModule.isActive()) {
    try { window.groupModule.stopGroup(); } catch (_) {}
    if (window._syncGroupIndicator) window._syncGroupIndicator(false);
  }

  const id = `pending-${++_pendingCounter}`;
  const tab = { id, sessionId: null, pending, title };
  _tabs.push(tab);
  _activeTabId = id;
  _render();

  window.sessionModule?.setCurrentSessionId?.(null);
  window.sessionModule?.setPendingChat?.(pending);

  const box = document.getElementById('chat-history');
  if (box) box.innerHTML = '';
  if (window.chatModule?.showWelcomeScreen) window.chatModule.showWelcomeScreen();

  if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();

  const metaEl = document.getElementById('current-meta');
  if (metaEl) metaEl.textContent = title;

  const msgInput = document.getElementById('message');
  if (msgInput) {
    msgInput.disabled = false;
    msgInput.value = '';
    msgInput.focus();
  }

  if (window.documentModule?.isPanelOpen?.() && window.documentModule.closePanel) {
    window.documentModule.closePanel();
  }
}

/** Switch to an existing session tab or open a new one. */
export async function switchToSession(sessionId, { keepSidebar = false } = {}) {
  if (!sessionId) return;
  _saveActiveTabState();

  let tab = _tabs.find(t => t.sessionId === sessionId);
  if (!tab) {
    const sm = window.sessionModule;
    const meta = sm?.getSessions?.()?.find(s => s.id === sessionId);
    tab = { id: sessionId, sessionId, pending: null, title: meta?.name || 'Chat' };
    _tabs.push(tab);
  }

  if (_activeTabId === tab.id) {
    _render();
    return;
  }

  await _activateTab(tab.id);
}

/** Ensure a tab exists for this session (e.g. on first load). */
export function ensureSessionTab(sessionId, title) {
  if (!sessionId) return;
  let tab = _tabs.find(t => t.sessionId === sessionId);
  if (!tab) {
    tab = { id: sessionId, sessionId, pending: null, title: title || 'Chat' };
    _tabs.push(tab);
  } else if (title) {
    tab.title = title;
  }
  _activeTabId = sessionId;
  _render();
}

/** Pending chat was materialized into a real session. */
export function onSessionMaterialized(sessionId, title) {
  const activeTab = _tabs.find(t => t.id === _activeTabId);
  if (activeTab && !activeTab.sessionId) {
    activeTab.id = sessionId;
    activeTab.sessionId = sessionId;
    activeTab.pending = null;
    activeTab.title = title || activeTab.title;
    _activeTabId = sessionId;
  } else if (!_tabs.some(t => t.sessionId === sessionId)) {
    _tabs.push({ id: sessionId, sessionId, pending: null, title: title || 'Chat' });
    _activeTabId = sessionId;
  }
  _render();
}

export function updateTabTitle(sessionId, title) {
  const tab = _tabs.find(t => t.sessionId === sessionId);
  if (tab && title) {
    tab.title = title;
    _render();
  }
}

/** Remove tab without stopping/deleting (e.g. sidebar delete already handled cleanup). */
export function removeTab(tabIdOrSessionId) {
  const idx = _findTabIndex(tabIdOrSessionId);
  const idxBySession = _tabs.findIndex(t => t.sessionId === tabIdOrSessionId);
  const i = idx >= 0 ? idx : idxBySession;
  if (i < 0) return;

  const wasActive = _tabs[i].id === _activeTabId;
  _tabs.splice(i, 1);
  if (wasActive) {
    _activeTabId = null;
    _activateAdjacentTab(Math.min(i, _tabs.length)).catch(() => {});
  } else {
    _render();
  }
}

/** Close tab: stop processes and delete the chat. */
export async function closeTab(tabId) {
  const idx = _findTabIndex(tabId);
  if (idx < 0) return;

  const tab = _tabs[idx];
  const wasActive = tab.id === _activeTabId;
  const sessionId = tab.sessionId;

  if (sessionId) {
    await _stopAndDeleteSession(sessionId);
  }

  _tabs.splice(idx, 1);

  if (wasActive) {
    _activeTabId = null;
    await _activateAdjacentTab(Math.min(idx, _tabs.length));
  } else {
    _render();
  }

  if (sessionId && window.sessionModule?.loadSessions) {
    await window.sessionModule.loadSessions().catch(() => {});
  }
}

export function getActivePending() {
  const tab = _tabs.find(t => t.id === _activeTabId);
  return tab?.pending || null;
}

export function setActivePending(pending) {
  const tab = _tabs.find(t => t.id === _activeTabId);
  if (tab) tab.pending = pending || null;
}

export function getOpenTabCount() {
  return _tabs.length;
}

const chatTabBar = {
  init,
  openNewPendingTab,
  switchToSession,
  ensureSessionTab,
  onSessionMaterialized,
  updateTabTitle,
  removeTab,
  closeTab,
  getActivePending,
  setActivePending,
  getOpenTabCount,
};

export default chatTabBar;
