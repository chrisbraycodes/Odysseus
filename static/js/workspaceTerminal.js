// static/js/workspaceTerminal.js
//
// Interactive workspace terminal — xterm.js over WebSocket PTY.

const XTERM_VERSION = '5.5.0';
const XTERM_CSS = `https://cdn.jsdelivr.net/npm/@xterm/xterm@${XTERM_VERSION}/css/xterm.css`;

let _xtermLoaded = null;

function _loadXterm() {
  if (_xtermLoaded) return _xtermLoaded;
  _xtermLoaded = (async () => {
    if (!document.querySelector('link[data-xterm-css]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = XTERM_CSS;
      link.setAttribute('data-xterm-css', '1');
      document.head.appendChild(link);
    }
    const [xterm, fitMod, webLinksMod] = await Promise.all([
      import(`https://cdn.jsdelivr.net/npm/@xterm/xterm@${XTERM_VERSION}/+esm`),
      import(`https://cdn.jsdelivr.net/npm/@xterm/addon-fit@${XTERM_VERSION}/+esm`),
      import(`https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@${XTERM_VERSION}/+esm`),
    ]);
    return {
      Terminal: xterm.Terminal,
      FitAddon: fitMod.FitAddon,
      WebLinksAddon: webLinksMod.WebLinksAddon,
    };
  })();
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
    fitAddon.fit();
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

    const params = new URLSearchParams({
      workspace,
      cols: String(term.cols),
      rows: String(term.rows),
    });
    if (sessionId) params.set('session', sessionId);

    socket = new WebSocket(`${_wsBase()}/api/terminal/ws?${params}`);
    socket.binaryType = 'arraybuffer';

    socket.addEventListener('open', () => {
      setStatus('Starting shell…', 'loading');
      _sendResize();
    });

    socket.addEventListener('message', (ev) => {
      if (disposed || !term) return;
      if (typeof ev.data === 'string') {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'status') {
            if (msg.session) sessionId = msg.session;
            if (msg.phase === 'ready') {
              setStatus(msg.message || 'Connected', 'ready');
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
        setStatus('Not authenticated — refresh and log in', 'error');
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

  return { connect, dispose, setStatus };
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

  addTab();

  return { addTab, closeTab, activateTab, dispose, reconnectAll };
}

export default { createWorkspaceTerminal, createWorkspaceTerminalPanel };
