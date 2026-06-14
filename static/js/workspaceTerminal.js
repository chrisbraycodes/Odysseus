// static/js/workspaceTerminal.js
//
// Interactive workspace terminal — xterm.js over WebSocket PTY (container or Windows host).

import { fetchHostTerminalStatus, isHostTerminalActive, isHostTerminalEnabled, getHostTerminalShell, saveHostTerminalShell } from './hostTerminal.js';

// Bundled locally — jsdelivr ESM imports hang/fail on many networks.
const XTERM_BASE = '/static/lib/xterm';

let _xtermLoaded = null;

function _loadScript(src, id, timeoutMs = 15000) {
  if (document.querySelector(`script[data-xterm="${id}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.setAttribute('data-xterm', id);
    const timer = window.setTimeout(() => reject(new Error(`Timed out loading ${id}`)), timeoutMs);
    s.onload = () => { window.clearTimeout(timer); resolve(); };
    s.onerror = () => {
      window.clearTimeout(timer);
      reject(new Error(`Failed to load ${id}`));
    };
    document.head.appendChild(s);
  });
}

function _loadXterm() {
  if (_xtermLoaded) return _xtermLoaded;
  _xtermLoaded = (async () => {
    if (!document.querySelector('link[data-xterm-css]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = `${XTERM_BASE}/xterm.css`;
      link.setAttribute('data-xterm-css', '1');
      document.head.appendChild(link);
    }
    await _loadScript(`${XTERM_BASE}/xterm.js`, 'core');
    await _loadScript(`${XTERM_BASE}/addon-fit.js`, 'fit');
    await _loadScript(`${XTERM_BASE}/addon-web-links.js`, 'web-links');
    const Terminal = globalThis.Terminal;
    const FitAddon = globalThis.FitAddon?.FitAddon ?? globalThis.FitAddon;
    const WebLinksAddon = globalThis.WebLinksAddon?.WebLinksAddon ?? globalThis.WebLinksAddon;
    if (!Terminal || !FitAddon || !WebLinksAddon) {
      throw new Error('xterm.js loaded but required globals are missing');
    }
    return { Terminal, FitAddon, WebLinksAddon };
  })().catch((err) => {
    _xtermLoaded = null;
    throw err;
  });
  return _xtermLoaded;
}

function _wsBase() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}`;
}

function _themeFromCss() {
  const style = getComputedStyle(document.documentElement);
  const pick = (v, fallback) => (style.getPropertyValue(v) || fallback).trim();
  return {
    background: pick('--bg', '#0a0a0a'),
    foreground: pick('--fg', '#e0e0e0'),
    cursor: pick('--brand-color', pick('--red', '#00ff41')),
    selectionBackground: pick('--panel', '#1a1a1a'),
  };
}

async function _writeClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fallback below */ }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try {
    return document.execCommand('copy');
  } catch (_) {
    return false;
  } finally {
    ta.remove();
  }
}

async function _readClipboard() {
  try {
    if (navigator.clipboard?.readText) {
      return await navigator.clipboard.readText();
    }
  } catch (_) { /* ignore */ }
  return '';
}

function _attachClipboardHandlers(term, termHost) {
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.type !== 'keydown') return true;
    const mod = ev.ctrlKey || ev.metaKey;
    const key = (ev.key || '').toLowerCase();

    // Copy selection: Ctrl+Shift+C, or Ctrl/Cmd+C when text is selected.
    if (mod && key === 'c') {
      if (ev.shiftKey || term.hasSelection()) {
        const sel = term.getSelection();
        if (sel) {
          ev.preventDefault();
          _writeClipboard(sel);
          return false;
        }
      }
      return true;
    }

    // Paste: Ctrl+Shift+V, Ctrl/Cmd+V, or Shift+Insert.
    const isPaste =
      (mod && key === 'v') ||
      (ev.shiftKey && key === 'insert');
    if (isPaste) {
      ev.preventDefault();
      _readClipboard().then((text) => {
        if (text) term.paste(text);
      });
      return false;
    }

    // Select all: Ctrl+Shift+A (common terminal shortcut).
    if (mod && ev.shiftKey && key === 'a') {
      ev.preventDefault();
      term.selectAll();
      return false;
    }

    return true;
  });

  termHost.addEventListener('mousedown', () => {
    window.setTimeout(() => term.focus(), 0);
  });
  termHost.addEventListener('dblclick', () => term.focus());
}

/**
 * @param {HTMLElement} mountEl — container for xterm + status bar
 * @param {{ workspace: string, onStatus?: (msg: string, phase: string) => void }} opts
 */
export function createWorkspaceTerminal(mountEl, opts) {
  const workspace = opts.workspace || '';
  let sessionId = '';
  let socket = null;
  let term = null;
  let fitAddon = null;
  let resizeObs = null;
  let disposed = false;
  let reconnectTimer = null;

  const statusEl = document.createElement('div');
  statusEl.className = 'ws-terminal-status';
  statusEl.setAttribute('role', 'status');
  statusEl.textContent = 'Loading terminal…';

  const termHost = document.createElement('div');
  termHost.className = 'ws-terminal-host';

  const toolbar = document.createElement('div');
  toolbar.className = 'ws-terminal-toolbar';

  const clipGroup = document.createElement('div');
  clipGroup.className = 'ws-terminal-clip-actions';

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'ws-terminal-clip-btn';
  copyBtn.textContent = 'Copy';
  copyBtn.title = 'Copy selection';

  const pasteBtn = document.createElement('button');
  pasteBtn.type = 'button';
  pasteBtn.className = 'ws-terminal-clip-btn';
  pasteBtn.textContent = 'Paste';
  pasteBtn.title = 'Paste from clipboard';

  clipGroup.appendChild(copyBtn);
  clipGroup.appendChild(pasteBtn);
  toolbar.appendChild(clipGroup);

  const shellWrap = document.createElement('div');
  shellWrap.className = 'ws-terminal-shell-wrap';
  shellWrap.hidden = true;
  const shellLabel = document.createElement('label');
  shellLabel.className = 'ws-terminal-shell-label';
  shellLabel.textContent = 'Shell';
  const shellSelect = document.createElement('select');
  shellSelect.className = 'ws-terminal-shell-select';
  shellSelect.title = 'Windows host shell (PowerShell or CMD)';
  shellSelect.innerHTML = '<option value="powershell">PowerShell</option><option value="cmd">CMD</option>';
  shellLabel.appendChild(shellSelect);
  shellWrap.appendChild(shellLabel);
  toolbar.appendChild(shellWrap);

  let hostShell = 'powershell';
  let hostUnrestricted = false;
  let lastConnectedHostShell = '';

  async function _syncShellToolbar(status) {
    const show = isHostTerminalEnabled(status);
    shellWrap.hidden = !show;
    if (!show) return;
    hostShell = getHostTerminalShell(status);
    hostUnrestricted = !!status?.host_terminal_unrestricted;
    shellSelect.value = hostShell;
  }

  shellSelect.addEventListener('change', async () => {
    const next = shellSelect.value === 'cmd' ? 'cmd' : 'powershell';
    if (next === hostShell) return;
    try {
      setStatus('Saving shell preference…', 'loading');
      await saveHostTerminalShell(next, { workspace, unrestricted: hostUnrestricted });
      hostShell = next;
      sessionId = '';
      lastConnectedHostShell = '';
      if (term) term.clear();
      await connect();
    } catch (err) {
      shellSelect.value = hostShell;
      setStatus(err.message || 'Could not save shell preference', 'error');
      reconnectBtn.style.display = '';
    }
  });

  const reconnectBtn = document.createElement('button');
  reconnectBtn.type = 'button';
  reconnectBtn.className = 'ws-terminal-reconnect';
  reconnectBtn.textContent = 'Reconnect';
  reconnectBtn.style.display = 'none';
  reconnectBtn.addEventListener('click', () => connect());
  toolbar.appendChild(reconnectBtn);

  mountEl.innerHTML = '';
  mountEl.appendChild(toolbar);
  mountEl.appendChild(statusEl);
  mountEl.appendChild(termHost);

  function setStatus(message, phase = 'info') {
    statusEl.textContent = message;
    statusEl.dataset.phase = phase;
    statusEl.classList.toggle('ws-terminal-status-error', phase === 'error');
    statusEl.classList.toggle('ws-terminal-status-ready', phase === 'ready');
    if (opts.onStatus) opts.onStatus(message, phase);
  }

  function _disconnect() {
    if (socket) {
      try { socket.close(); } catch (_) { /* ignore */ }
      socket = null;
    }
  }

  async function _ensureTerm() {
    if (term) return;
    setStatus('Loading xterm.js…', 'loading');
    const { Terminal, FitAddon, WebLinksAddon } = await _loadXterm();
    term = new Terminal({
      cursorBlink: true,
      fontFamily: 'Consolas, "Cascadia Mono", "Courier New", monospace',
      fontSize: 12,
      theme: _themeFromCss(),
      allowProposedApi: true,
    });
    fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(termHost);
    _attachClipboardHandlers(term, termHost);
    copyBtn.addEventListener('click', () => {
      const sel = term.getSelection();
      if (sel) {
        _writeClipboard(sel);
        return;
      }
      term.selectAll();
      _writeClipboard(term.getSelection());
    });
    pasteBtn.addEventListener('click', () => {
      term.focus();
      _readClipboard().then((text) => {
        if (text) term.paste(text);
      });
    });
    fitAddon.fit();
    term.focus();
    term.onData((data) => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(new TextEncoder().encode(data));
      }
    });
    resizeObs = new ResizeObserver(() => {
      if (!fitAddon || !term) return;
      fitAddon.fit();
      _sendResize();
    });
    resizeObs.observe(termHost);
    term.onResize(() => _sendResize());
  }

  function fit() {
    if (!fitAddon || !term) return;
    try { fitAddon.fit(); } catch (_) { /* ignore */ }
    _sendResize();
  }

  function _sendResize() {
    if (!socket || socket.readyState !== WebSocket.OPEN || !term) return;
    socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
  }

  async function connect() {
    if (disposed) return;
    reconnectBtn.style.display = 'none';
    _disconnect();
    if (!workspace) {
      setStatus('Select a workspace folder first', 'error');
      return;
    }
    await _ensureTerm();
    setStatus('Connecting…', 'loading');

    let useHost = false;
    try {
      const status = await fetchHostTerminalStatus(workspace);
      useHost = isHostTerminalActive(status);
      await _syncShellToolbar(status);
      if (useHost) {
        hostShell = shellSelect.value === 'cmd' ? 'cmd' : getHostTerminalShell(status);
        shellSelect.value = hostShell;
      }
    } catch (_) {
      useHost = false;
      shellWrap.hidden = true;
    }

    if (useHost && lastConnectedHostShell && lastConnectedHostShell !== hostShell) {
      sessionId = '';
    }

    const params = new URLSearchParams({
      workspace,
      cols: String(term.cols),
      rows: String(term.rows),
    });
    if (useHost) {
      params.set('host', '1');
      params.set('shell', hostShell);
      sessionId = '';
      if (term) term.clear();
    } else if (sessionId) {
      params.set('session', sessionId);
    }

    socket = new WebSocket(`${_wsBase()}/api/terminal/ws?${params}`);
    socket.binaryType = 'arraybuffer';

    socket.addEventListener('open', () => {
      const shellName = hostShell === 'cmd' ? 'CMD' : 'PowerShell';
      setStatus(
        useHost ? `Starting Windows ${shellName}…` : 'Starting shell…',
        'loading',
      );
      _sendResize();
    });

    socket.addEventListener('message', (ev) => {
      if (disposed || !term) return;
      if (typeof ev.data === 'string') {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'status') {
            if (msg.session && !useHost) sessionId = msg.session;
            if (msg.phase === 'ready') {
              if (useHost) {
                lastConnectedHostShell = msg.shell || hostShell;
                const label = lastConnectedHostShell === 'cmd' ? 'CMD' : 'PowerShell';
                setStatus(msg.message || `Connected (${label})`, 'ready');
              } else {
                setStatus(msg.message || 'Connected', 'ready');
              }
              term.focus();
              window.setTimeout(() => {
                if (statusEl.dataset.phase === 'ready') {
                  statusEl.style.display = 'none';
                }
              }, 1200);
            } else if (msg.phase === 'error') {
              setStatus(msg.message || 'Terminal error', 'error');
              reconnectBtn.style.display = '';
            } else {
              setStatus(msg.message || 'Loading…', 'loading');
            }
          }
        } catch (_) {
          term.write(ev.data);
        }
        return;
      }
      const buf = ev.data instanceof ArrayBuffer ? new Uint8Array(ev.data) : ev.data;
      term.write(buf);
    });

    socket.addEventListener('close', (ev) => {
      if (disposed) return;
      const reason = ev.reason || 'Disconnected';
      if (ev.code === 4403) {
        setStatus('Terminal requires admin access', 'error');
      } else if (ev.code === 4503) {
        setStatus(reason || 'Install pywinpty: pip install pywinpty', 'error');
      } else if (ev.code === 4401) {
        setStatus('Terminal connection rejected — refresh the page', 'error');
      } else if (ev.code !== 1000) {
        setStatus(reason, 'error');
      } else {
        setStatus('Disconnected', 'info');
      }
      reconnectBtn.style.display = '';
      statusEl.style.display = '';
    });

    socket.addEventListener('error', () => {
      if (!disposed) setStatus('WebSocket connection failed', 'error');
    });
  }

  function dispose() {
    disposed = true;
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    _disconnect();
    resizeObs?.disconnect();
    resizeObs = null;
    term?.dispose();
    term = null;
    fitAddon = null;
  }

  connect().catch((err) => {
    setStatus(err.message || 'Failed to start terminal', 'error');
    reconnectBtn.style.display = '';
  });

  return { connect, dispose, setStatus, focus: () => term?.focus(), fit, _fit: fit };
}

/**
 * Multi-tab terminal panel for the workspace explorer.
 */
export function createWorkspaceTerminalPanel(mountEl, opts) {
  const workspace = opts.workspace || '';
  let tabCounter = 0;
  /** @type {{ id: string, label: string, host: HTMLElement, term: ReturnType<typeof createWorkspaceTerminal> | null }[]} */
  const tabs = [];
  let activeId = null;

  const tabBar = document.createElement('div');
  tabBar.className = 'ws-terminal-tabs';

  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'ws-terminal-tab-add';
  addBtn.title = 'New terminal tab';
  addBtn.textContent = '+';
  addBtn.addEventListener('click', () => addTab());

  const body = document.createElement('div');
  body.className = 'ws-terminal-panel-body';

  mountEl.innerHTML = '';
  mountEl.appendChild(tabBar);
  mountEl.appendChild(body);

  function _renderTabs() {
    tabBar.innerHTML = '';
    tabs.forEach((t) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ws-terminal-tab' + (t.id === activeId ? ' active' : '');
      btn.title = t.label;
      btn.addEventListener('click', () => activateTab(t.id));
      const labelSpan = document.createElement('span');
      labelSpan.className = 'ws-terminal-tab-label';
      labelSpan.textContent = t.label;
      btn.appendChild(labelSpan);
      const close = document.createElement('span');
      close.className = 'ws-terminal-tab-close';
      close.textContent = '×';
      close.title = 'Close tab';
      close.addEventListener('click', (e) => {
        e.stopPropagation();
        closeTab(t.id);
      });
      btn.appendChild(close);
      tabBar.appendChild(btn);
    });
    tabBar.appendChild(addBtn);
  }

  function activateTab(id) {
    activeId = id;
    tabs.forEach((t) => {
      t.host.style.display = t.id === id ? 'flex' : 'none';
    });
    _renderTabs();
    const active = tabs.find((t) => t.id === id);
    if (active?.term) {
      window.setTimeout(() => {
        try { active.term._fit?.(); } catch (_) { /* optional */ }
      }, 50);
    }
  }

  function closeTab(id) {
    const idx = tabs.findIndex((t) => t.id === id);
    if (idx === -1) return;
    const [removed] = tabs.splice(idx, 1);
    removed.term?.dispose();
    removed.host.remove();
    if (activeId === id) {
      const next = tabs[idx] || tabs[idx - 1] || tabs[0];
      activeId = next ? next.id : null;
      if (next) activateTab(next.id);
      else addTab();
    }
    _renderTabs();
  }

  function addTab() {
    tabCounter += 1;
    const id = `term-${tabCounter}-${Date.now()}`;
    const host = document.createElement('div');
    host.className = 'ws-terminal-tab-pane';
    host.style.display = 'none';
    body.appendChild(host);
    const label = `Terminal ${tabCounter}`;
    const term = createWorkspaceTerminal(host, { workspace });
    const entry = { id, label, host, term };
    tabs.push(entry);
    activateTab(id);
    return id;
  }

  function dispose() {
    document.removeEventListener('ws-terminal-layout', fitAll);
    window.removeEventListener('resize', fitAll);
    tabs.forEach((t) => t.term?.dispose());
    tabs.length = 0;
    mountEl.innerHTML = '';
  }

  function reconnectAll() {
    const ids = tabs.map((t) => t.id);
    tabs.forEach((t) => {
      t.term?.dispose();
      t.host.innerHTML = '';
    });
    tabs.length = 0;
    tabCounter = 0;
    ids.forEach(() => addTab());
  }

  function fitAll() {
    tabs.forEach((t) => {
      try { t.term?.fit?.(); } catch (_) { /* ignore */ }
    });
  }

  addTab();

  document.addEventListener('ws-terminal-layout', fitAll);
  window.addEventListener('resize', fitAll);

  return {
    addTab,
    closeTab,
    activateTab,
    dispose,
    reconnectAll,
    fitAll,
    _onLayout: fitAll,
  };
}

export default { createWorkspaceTerminal, createWorkspaceTerminalPanel };
