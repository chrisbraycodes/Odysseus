// static/js/hostTerminal.js — Windows host terminal consent, chat banner, risk modal.

import Storage, { KEYS } from './storage.js';
import uiModule from './ui.js';
import { getWorkspace, getVerifiedWorkspace, whenWorkspaceReady } from './workspace.js';

const API_BASE = window.location.origin;

let _bannerEl = null;

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s) : String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export async function fetchHostTerminalStatus(workspace = '') {
  const ws = workspace || getWorkspace();
  const q = ws ? `?workspace=${encodeURIComponent(ws)}` : '';
  const res = await fetch(`${API_BASE}/api/workspace/host-terminal/status${q}`, {
    credentials: 'same-origin',
  });
  if (!res.ok) throw new Error(`host terminal status failed: ${res.status}`);
  return res.json();
}

export function isHostTerminalEnabled(status) {
  return !!status?.host_terminal_enabled;
}

export function isHostTerminalActive(status) {
  return !!(status?.host_terminal_enabled && status?.host_agent_reachable);
}

export function getHostTerminalShell(status) {
  const shell = status?.host_terminal_shell || status?.consent?.shell || 'powershell';
  return shell === 'cmd' ? 'cmd' : 'powershell';
}

export async function saveHostTerminalShell(shell, { workspace = '', unrestricted = false } = {}) {
  const ws = workspace || getWorkspace();
  if (!ws) throw new Error('Pick a workspace folder first');
  const res = await fetch(`${API_BASE}/api/workspace/host-terminal/consent`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      accepted: true,
      unrestricted,
      workspace: ws,
      shell: shell === 'cmd' ? 'cmd' : 'powershell',
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `shell preference failed: ${res.status}`);
  }
  return res.json();
}

export function showWorkspaceRiskModal({ onConfirm, onCancel } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal host-risk-modal';
    overlay.style.display = 'flex';
    overlay.innerHTML = `
      <div class="modal-content host-risk-modal-content">
        <div class="modal-header">
          <h4>Workspace safety warning</h4>
        </div>
        <div class="host-risk-body">
          <p><strong>Odysseus can modify files on your computer</strong> in the folder you select.</p>
          <p>Shell commands, npm installs, and automated edits can <strong>delete or corrupt data</strong>
             on your machine — including outside the project if you later enable unrestricted host access.</p>
          <p><strong>Back up your computer</strong> before continuing. We are not liable for lost or damaged files.</p>
          <label class="host-risk-check">
            <input type="checkbox" id="host-risk-ack" />
            I understand the risks and have backed up important data
          </label>
        </div>
        <div class="modal-footer workspace-footer">
          <button type="button" class="confirm-btn confirm-btn-secondary" id="host-risk-cancel">Cancel</button>
          <button type="button" class="confirm-btn confirm-btn-primary" id="host-risk-confirm" disabled>Use this folder</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const ack = overlay.querySelector('#host-risk-ack');
    const confirmBtn = overlay.querySelector('#host-risk-confirm');
    const close = (accepted) => {
      overlay.remove();
      resolve(accepted);
      if (accepted) onConfirm?.();
      else onCancel?.();
    };
    ack?.addEventListener('change', () => {
      if (confirmBtn) confirmBtn.disabled = !ack.checked;
    });
    overlay.querySelector('#host-risk-cancel')?.addEventListener('click', () => close(false));
    overlay.querySelector('#host-risk-confirm')?.addEventListener('click', () => {
      if (!ack?.checked) return;
      Storage.setJSON(KEYS.WORKSPACE_RISK_ACK, { at: Date.now() });
      close(true);
    });
  });
}

export async function confirmWorkspaceRiskIfNeeded({ docker = false } = {}) {
  if (!docker) return true;
  return showWorkspaceRiskModal();
}

function _syncHostTerminalConfirm(overlay) {
  const ack = overlay.querySelector('#host-terminal-ack');
  const confirmBtn = overlay.querySelector('#host-terminal-confirm');
  const hint = overlay.querySelector('#host-terminal-enable-hint');
  const on = !!(ack && ack.checked);
  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.classList.toggle('host-risk-btn-muted', !on);
    confirmBtn.setAttribute('aria-disabled', on ? 'false' : 'true');
  }
  if (hint) hint.hidden = on;
}

export function showHostTerminalConsentModal({ workspace, unrestricted = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal host-risk-modal';
    overlay.style.display = 'flex';
    overlay.innerHTML = `
      <div class="modal-content host-risk-modal-content">
        <div class="modal-header">
          <h4>Enable Windows host terminal</h4>
        </div>
        <div class="host-risk-body">
          <p>This connects the Odysseus terminal to a <strong>real Windows shell</strong> on your computer.</p>
          <p>By default, automated npm/node commands and file edits stay inside your selected workspace folder.</p>
          <p><strong>Warning:</strong> mistakes or malicious prompts can still damage your computer.
             Back up your data first. We are not liable for lost or corrupted files.</p>
          <label class="host-risk-check host-risk-check-required">
            <input type="checkbox" id="host-terminal-ack" />
            <span>I accept these risks and want the Windows host terminal <em>(required)</em></span>
          </label>
          <p class="host-risk-enable-hint" id="host-terminal-enable-hint">
            Check the required box above, then click Enable.
          </p>
          <label class="host-risk-check host-risk-check-muted">
            <input type="checkbox" id="host-terminal-unrestricted" ${unrestricted ? 'checked' : ''} />
            <span>Allow commands and file edits <strong>outside</strong> the workspace folder (optional, advanced)</span>
          </label>
          <label class="host-risk-shell">
            <span>Windows shell</span>
            <select id="host-terminal-shell">
              <option value="powershell" selected>PowerShell</option>
              <option value="cmd">CMD</option>
            </select>
          </label>
        </div>
        <div class="modal-footer workspace-footer">
          <button type="button" class="confirm-btn confirm-btn-secondary" id="host-terminal-cancel">Cancel</button>
          <button type="button" class="confirm-btn confirm-btn-primary" id="host-terminal-confirm">Enable</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const ack = overlay.querySelector('#host-terminal-ack');
    const confirmBtn = overlay.querySelector('#host-terminal-confirm');
    const unrestrictedEl = overlay.querySelector('#host-terminal-unrestricted');
    const close = () => overlay.remove();
    const onAck = () => _syncHostTerminalConfirm(overlay);
    ack?.addEventListener('change', onAck);
    ack?.addEventListener('input', onAck);
    ack?.addEventListener('click', () => window.setTimeout(onAck, 0));
    _syncHostTerminalConfirm(overlay);
    overlay.querySelector('#host-terminal-cancel')?.addEventListener('click', () => {
      close();
      resolve(null);
    });
    confirmBtn?.addEventListener('click', async () => {
      if (!ack?.checked) {
        onAck();
        ack?.focus();
        if (uiModule?.showToast) uiModule.showToast('Check the required agreement box first');
        return;
      }
      if (confirmBtn.dataset.busy === '1') return;
      confirmBtn.dataset.busy = '1';
      confirmBtn.textContent = 'Enabling…';
      const ws = workspace || getWorkspace();
      try {
        const res = await fetch(`${API_BASE}/api/workspace/host-terminal/consent`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            accepted: true,
            unrestricted: !!unrestrictedEl?.checked,
            workspace: ws,
            shell: overlay.querySelector('#host-terminal-shell')?.value || 'powershell',
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `consent failed: ${res.status}`);
        }
        const data = await res.json();
        close();
        if (data.warning) {
          if (uiModule?.showError) uiModule.showError(data.warning);
          else if (uiModule?.showToast) uiModule.showToast(data.warning, 12000);
        } else if (uiModule?.showToast) {
          uiModule.showToast('Windows host terminal enabled');
        }
        try {
          document.dispatchEvent(new CustomEvent('host-terminal-enabled', { detail: data }));
        } catch (_) {}
        resolve(data);
      } catch (e) {
        confirmBtn.dataset.busy = '0';
        confirmBtn.textContent = 'Enable';
        if (uiModule?.showError) uiModule.showError(e.message || 'Could not enable host terminal');
      }
    });
  });
}

export async function enableHostTerminalFromChat() {
  await whenWorkspaceReady();
  const ws = getWorkspace();
  if (!ws) {
    if (uiModule?.showError) uiModule.showError('Pick a workspace folder first');
    return false;
  }
  const result = await showHostTerminalConsentModal({ workspace: ws });
  if (result) refreshHostTerminalBanner();
  return !!result;
}

export function refreshHostTerminalBanner() {
  const chatBar = document.querySelector('.chat-input-bar');
  if (!chatBar) return;
  fetchHostTerminalStatus()
    .then((status) => {
      if (_bannerEl) {
        _bannerEl.remove();
        _bannerEl = null;
      }
      if (!status.host_agent_configured) return;
      if (isHostTerminalEnabled(status)) {
        if (!isHostTerminalActive(status) && status.host_agent_error) {
          _bannerEl = document.createElement('div');
          _bannerEl.id = 'host-terminal-banner';
          _bannerEl.className = 'host-terminal-banner host-terminal-banner-warn';
            _bannerEl.innerHTML = `
            <span>Host terminal is enabled, but the Windows agent is not running. Run <code>start.bat</code> or <code>start-host-agent.bat</code>.</span>
            <button type="button" class="host-terminal-banner-dismiss" aria-label="Dismiss">✕</button>`;
          chatBar.parentNode?.insertBefore(_bannerEl, chatBar);
          _bannerEl.querySelector('.host-terminal-banner-dismiss')?.addEventListener('click', () => {
            _bannerEl?.remove();
            _bannerEl = null;
          });
        }
        return;
      }
      if (!getWorkspace()) return;

      _bannerEl = document.createElement('div');
      _bannerEl.id = 'host-terminal-banner';
      _bannerEl.className = 'host-terminal-banner';
      _bannerEl.innerHTML = `
        <span>Run npm and dev servers on your Windows computer (not inside Docker).</span>
        <button type="button" class="host-terminal-banner-btn" id="host-terminal-enable-btn">Enable Windows host terminal</button>
        <button type="button" class="host-terminal-banner-dismiss" aria-label="Dismiss">✕</button>`;
      chatBar.parentNode?.insertBefore(_bannerEl, chatBar);
      _bannerEl.querySelector('#host-terminal-enable-btn')?.addEventListener('click', () => {
        enableHostTerminalFromChat();
      });
      _bannerEl.querySelector('.host-terminal-banner-dismiss')?.addEventListener('click', () => {
        _bannerEl?.remove();
        _bannerEl = null;
        Storage.setJSON(KEYS.HOST_TERMINAL_BANNER_DISMISSED, { at: Date.now() });
      });
    })
    .catch(() => {});
}

export function initHostTerminalUi() {
  whenWorkspaceReady().then(() => {
    if (Storage.getJSON(KEYS.HOST_TERMINAL_BANNER_DISMISSED, null)) return;
    refreshHostTerminalBanner();
  });
  document.addEventListener('workspace-verified', () => refreshHostTerminalBanner());
  document.addEventListener('workspace-changed', () => refreshHostTerminalBanner());
  document.addEventListener('host-terminal-enabled', () => {
    if (_bannerEl) {
      _bannerEl.remove();
      _bannerEl = null;
    }
    try {
      document.dispatchEvent(new CustomEvent('prepare-workspace-terminal'));
    } catch (_) {}
  });
}

export default {
  fetchHostTerminalStatus,
  isHostTerminalEnabled,
  isHostTerminalActive,
  getHostTerminalShell,
  saveHostTerminalShell,
  showWorkspaceRiskModal,
  confirmWorkspaceRiskIfNeeded,
  showHostTerminalConsentModal,
  enableHostTerminalFromChat,
  refreshHostTerminalBanner,
  initHostTerminalUi,
};
