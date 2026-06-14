/**
 * Odysseus IDE / chat performance debugger.
 *
 * Load in the browser console (after the app boots):
 *   const s = document.createElement('script');
 *   s.src = '/static/js/perfDebug.js?v=' + Date.now();
 *   document.head.appendChild(s);
 *
 * Or: await import('/static/js/perfDebug.js?v=' + Date.now());
 *
 * Commands (global: window.__odysseusPerfDebug):
 *   .start()          — begin instrumentation (auto-starts on load)
 *   .stop()           — remove hooks, keep collected data
 *   .report()         — print ranked summary to console
 *   .reportHtml()     — open a floating panel with live stats
 *   .reset()          — clear counters
 *   .snapshot()        — DOM + memory snapshot now
 *   .traceNext(n)     — capture stack traces for next n loadSessions/selectSession
 */
(function () {
  'use strict';

  if (window.__odysseusPerfDebug && window.__odysseusPerfDebug._running) {
    console.warn('[perfDebug] already running — call __odysseusPerfDebug.report()');
    return;
  }

  const START = performance.now();
  const LOAD_AGE_MS = Date.now() - (window._odysseusLoadTime || Date.now());

  const stats = {
    longTasks: [],
    fetches: new Map(),
    fetchSlow: [],
    hooks: {
      loadSessions: { count: 0, totalMs: 0, maxMs: 0, traces: [] },
      selectSession: { count: 0, totalMs: 0, maxMs: 0, traces: [] },
      renderSessionList: { count: 0, totalMs: 0, maxMs: 0 },
      scrollHistory: { count: 0, windowStart: 0, windowCount: 0, peakPerSec: 0 },
      syncHighlighting: { count: 0, totalMs: 0 },
      renderTree: { count: 0, totalMs: 0 },
      addMessage: { count: 0, totalMs: 0, maxMs: 0 },
      backgroundStreamPoll: 0,
      innerHTML: { chatHistory: 0, sessionList: 0, wsTree: 0, chatTabBar: 0, other: 0 },
    },
    timers: {
      intervalsCreated: 0,
      timeoutsCreated: 0,
      rafCreated: 0,
      rafPerSecPeak: 0,
      fastIntervals: new Map(),
    },
    observers: { mutationCreated: 0, callbackFires: 0, callbackMs: 0 },
    layout: { reads: 0, writes: 0, forcedReflows: 0 },
    domSnapshots: [],
  };

  const originals = {};
  let active = false;
  let traceBudget = 0;
  let panelEl = null;
  let panelTimer = null;
  let rafWindowStart = performance.now();
  let rafWindowCount = 0;

  const HOT_FETCH = /\/api\/(sessions|history|research|chat\/stream|memory|documents|workspace)/;
  const LAYOUT_READ_PROPS = new Set([
    'offsetWidth', 'offsetHeight', 'offsetTop', 'offsetLeft',
    'clientWidth', 'clientHeight', 'scrollWidth', 'scrollHeight',
    'getBoundingClientRect', 'getComputedStyle',
  ]);

  function ms(n) { return (n || 0).toFixed(1) + 'ms'; }
  function pct(part, total) { return total ? ((part / total) * 100).toFixed(0) + '%' : '0%'; }

  function bumpFetch(url, dur, ok) {
    const key = String(url).split('?')[0].replace(location.origin, '');
    const row = stats.fetches.get(key) || { count: 0, totalMs: 0, maxMs: 0, errors: 0 };
    row.count++;
    row.totalMs += dur;
    row.maxMs = Math.max(row.maxMs, dur);
    if (!ok) row.errors++;
    stats.fetches.set(key, row);
    if (dur >= 400) stats.fetchSlow.push({ url: key, dur, at: performance.now() - START });
  }

  function recordHook(name, dur, extra) {
    const h = stats.hooks[name];
    if (!h) return;
    h.count++;
    h.totalMs += dur;
    h.maxMs = Math.max(h.maxMs, dur);
    if (traceBudget > 0 && (name === 'loadSessions' || name === 'selectSession')) {
      h.traces.push({ dur, at: performance.now() - START, stack: new Error().stack });
      traceBudget--;
    }
    if (extra) Object.assign(h, extra);
  }

  function wrapFn(obj, key, hookName, before) {
    if (!obj || typeof obj[key] !== 'function') return false;
    const orig = obj[key].bind(obj);
    originals[`${hookName}:${key}`] = orig;
    obj[key] = async function (...args) {
      before?.(...args);
      const t0 = performance.now();
      try {
        return await orig(...args);
      } finally {
        recordHook(hookName, performance.now() - t0);
      }
    };
    return true;
  }

  function wrapSyncFn(obj, key, hookName) {
    if (!obj || typeof obj[key] !== 'function') return false;
    const orig = obj[key].bind(obj);
    originals[`${hookName}:${key}`] = orig;
    obj[key] = function (...args) {
      const t0 = performance.now();
      try {
        return orig(...args);
      } finally {
        recordHook(hookName, performance.now() - t0);
      }
    };
    return true;
  }

  function hookInnerHTML(el, bucket) {
    if (!el || el.__odyPerfInnerHTML) return;
    const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
    if (!desc || !desc.set) return;
    el.__odyPerfInnerHTML = true;
    Object.defineProperty(el, 'innerHTML', {
      configurable: true,
      enumerable: desc.enumerable,
      get() { return desc.get.call(this); },
      set(v) {
        stats.hooks.innerHTML[bucket]++;
        stats.layout.writes++;
        return desc.set.call(this, v);
      },
    });
  }

  function hookSessionModule() {
    const sm = window.sessionModule;
    if (!sm) return false;
    wrapFn(sm, 'loadSessions', 'loadSessions');
    wrapFn(sm, 'selectSession', 'selectSession');
    wrapSyncFn(sm, 'renderSessionList', 'renderSessionList');
    return true;
  }

  function hookUiModule() {
    const ui = window.uiModule;
    if (!ui) return false;
    wrapSyncFn(ui, 'scrollHistory', 'scrollHistory');
    const orig = ui.scrollHistory;
    const base = originals['scrollHistory:scrollHistory'];
    if (base) {
      ui.scrollHistory = function (...args) {
        base(...args);
        const h = stats.hooks.scrollHistory;
        h.count++;
        h.windowCount++;
        const now = performance.now();
        if (now - h.windowStart >= 1000) {
          h.peakPerSec = Math.max(h.peakPerSec, h.windowCount);
          h.windowCount = 0;
          h.windowStart = now;
        }
      };
    }
    return true;
  }

  function hookDocumentModule() {
    const doc = window.documentModule;
    if (!doc || !doc._syncHighlightingHooked) {
      // syncHighlighting is not exported; watch via DOM if editor present later
    }
    return false;
  }

  function hookChatModule() {
    const chat = window.chatModule;
    if (!chat) return false;
    let hooked = false;
    if (typeof chat.checkBackgroundStream === 'function' && !originals['chat:checkBackgroundStream']) {
      const orig = chat.checkBackgroundStream.bind(chat);
      originals['chat:checkBackgroundStream'] = orig;
      chat.checkBackgroundStream = function (...args) {
        stats.hooks.backgroundStreamPoll = (stats.hooks.backgroundStreamPoll || 0) + 1;
        return orig(...args);
      };
      hooked = true;
    }
    if (typeof chat.addMessage === 'function' && !originals['chat:addMessage']) {
      const orig = chat.addMessage.bind(chat);
      originals['chat:addMessage'] = orig;
      chat.addMessage = function (...args) {
        const t0 = performance.now();
        try {
          return orig(...args);
        } finally {
          recordHook('addMessage', performance.now() - t0);
        }
      };
      hooked = true;
    }
    return hooked;
  }

  function hookWorkspaceExplorer() {
    const w = window.workspaceExplorerModule || window.wsExplorer;
    if (!w || typeof w._renderTree !== 'function') return false;
    wrapSyncFn(w, '_renderTree', 'renderTree');
    return true;
  }

  function hookFetch() {
    if (originals.fetch) return;
    originals.fetch = window.fetch;
    window.fetch = async function (...args) {
      const url = String(args[0]);
      const t0 = performance.now();
      let ok = true;
      try {
        const res = await originals.fetch.apply(this, args);
        ok = res.ok;
        return res;
      } catch (e) {
        ok = false;
        throw e;
      } finally {
        const dur = performance.now() - t0;
        if (HOT_FETCH.test(url) || dur >= 200) bumpFetch(url, dur, ok);
      }
    };
  }

  function hookTimers() {
    if (originals.setInterval) return;
    originals.setInterval = window.setInterval;
    originals.clearInterval = window.clearInterval;
    originals.setTimeout = window.setTimeout;
    originals.clearTimeout = window.clearTimeout;
    originals.requestAnimationFrame = window.requestAnimationFrame;

    window.setInterval = function (fn, delay, ...rest) {
      stats.timers.intervalsCreated++;
      const msDelay = Number(delay) || 0;
      if (msDelay > 0 && msDelay <= 2000 && typeof fn === 'function') {
        const key = msDelay + 'ms';
        const row = stats.timers.fastIntervals.get(key) || { delay: msDelay, count: 0, fires: 0 };
        row.count++;
        const wrapped = function (...a) {
          row.fires++;
          return fn.apply(this, a);
        };
        stats.timers.fastIntervals.set(key, row);
        return originals.setInterval.call(this, wrapped, delay, ...rest);
      }
      return originals.setInterval.call(this, fn, delay, ...rest);
    };
    window.setTimeout = function (...args) {
      stats.timers.timeoutsCreated++;
      return originals.setTimeout.apply(this, args);
    };
    window.requestAnimationFrame = function (cb) {
      stats.timers.rafCreated++;
      rafWindowCount++;
      const now = performance.now();
      if (now - rafWindowStart >= 1000) {
        stats.timers.rafPerSecPeak = Math.max(stats.timers.rafPerSecPeak, rafWindowCount);
        rafWindowCount = 0;
        rafWindowStart = now;
      }
      return originals.requestAnimationFrame.call(this, cb);
    };
  }

  function hookMutationObserver() {
    if (originals.MutationObserver) return;
    originals.MutationObserver = window.MutationObserver;
    window.MutationObserver = function (callback) {
      stats.observers.mutationCreated++;
      const wrapped = function (mutations, observer) {
        const t0 = performance.now();
        try {
          return callback(mutations, observer);
        } finally {
          stats.observers.callbackFires++;
          stats.observers.callbackMs += performance.now() - t0;
        }
      };
      return new originals.MutationObserver(wrapped);
    };
    window.MutationObserver.prototype = originals.MutationObserver.prototype;
  }

  function hookLayoutReads() {
    if (originals.layoutHooked) return;
    originals.layoutHooked = true;

    const origGbcr = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function (...args) {
      stats.layout.reads++;
      return origGbcr.apply(this, args);
    };

    const origGcs = window.getComputedStyle;
    window.getComputedStyle = function (...args) {
      stats.layout.reads++;
      return origGcs.apply(this, args);
    };

    // Detect forced reflow pattern: write then immediate read
    let lastWriteAt = 0;
    const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
    if (desc && desc.set) {
      const origSet = desc.set;
      Object.defineProperty(Element.prototype, 'innerHTML', {
        configurable: true,
        enumerable: desc.enumerable,
        get: desc.get,
        set(v) {
          stats.layout.writes++;
          lastWriteAt = performance.now();
          return origSet.call(this, v);
        },
      });
    }

    const readProps = ['offsetWidth', 'offsetHeight', 'scrollHeight', 'clientHeight'];
    for (const prop of readProps) {
      const d = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
      if (!d || !d.get) continue;
      Object.defineProperty(HTMLElement.prototype, prop, {
        configurable: true,
        enumerable: d.enumerable,
        get() {
          stats.layout.reads++;
          if (lastWriteAt && performance.now() - lastWriteAt < 16) stats.layout.forcedReflows++;
          return d.get.call(this);
        },
      });
    }
  }

  function hookLongTasks() {
    if (!('PerformanceObserver' in window)) return;
    try {
      const obs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (e.duration < 40) continue;
          stats.longTasks.push({
            dur: e.duration,
            at: e.startTime,
            name: e.name,
          });
          if (stats.longTasks.length > 200) stats.longTasks.shift();
        }
      });
      obs.observe({ type: 'longtask', buffered: true });
      originals.longTaskObs = obs;
    } catch (_) { /* unsupported */ }
  }

  function hookDomTargets() {
    hookInnerHTML(document.getElementById('chat-history'), 'chatHistory');
    hookInnerHTML(document.getElementById('session-list'), 'sessionList');
    hookInnerHTML(document.getElementById('ws-tree-body'), 'wsTree');
    hookInnerHTML(document.getElementById('chat-tab-bar'), 'chatTabBar');
  }

  function domSnapshot(label) {
    const snap = {
      label: label || 'snapshot',
      at: performance.now() - START,
      nodes: document.getElementsByTagName('*').length,
      chatMsgs: document.querySelectorAll('#chat-history .msg').length,
      sessionRows: document.querySelectorAll('#session-list .session-item, #session-list li').length,
      bodyDropdowns: document.querySelectorAll('.session-dropdown, .folder-submenu').length,
      modals: document.querySelectorAll('.modal-overlay, .modal-backdrop').length,
      preBlocks: document.querySelectorAll('#chat-history pre code').length,
      hljsBlocks: document.querySelectorAll('#chat-history pre code.hljs').length,
      memory: performance.memory ? {
        usedMB: (performance.memory.usedJSHeapSize / 1048576).toFixed(1),
        totalMB: (performance.memory.totalJSHeapSize / 1048576).toFixed(1),
      } : null,
    };
    stats.domSnapshots.push(snap);
    if (stats.domSnapshots.length > 50) stats.domSnapshots.shift();
    return snap;
  }

  function rankFetches() {
    return [...stats.fetches.entries()]
      .map(([url, row]) => ({ url, ...row, avgMs: row.totalMs / row.count }))
      .sort((a, b) => b.totalMs - a.totalMs);
  }

  function diagnose() {
    const issues = [];
    const h = stats.hooks;

    if (h.loadSessions.count >= 5 && h.loadSessions.count / ((performance.now() - START) / 60000) > 8) {
      issues.push({
        severity: 'HIGH',
        area: 'sessions',
        msg: `loadSessions called ${h.loadSessions.count} times (${ms(h.loadSessions.totalMs)} total) — sidebar full rebuild storm`,
        fix: 'Debounce loadSessions(); skip render when metadata unchanged',
      });
    }
    if (h.selectSession.maxMs > 300) {
      issues.push({
        severity: 'HIGH',
        area: 'chat',
        msg: `selectSession peaked at ${ms(h.selectSession.maxMs)} — full history re-fetch + re-render + hljs`,
        fix: 'Cache per-session DOM; incremental history append',
      });
    }
    if (h.scrollHistory.peakPerSec > 15) {
      issues.push({
        severity: 'HIGH',
        area: 'chat',
        msg: `scrollHistory peaked at ${h.scrollHistory.peakPerSec}/sec during streaming`,
        fix: 'Throttle scroll during SSE to ≤4/sec',
      });
    }
    if (stats.timers.rafPerSecPeak > 30) {
      issues.push({
        severity: 'HIGH',
        area: 'chat',
        msg: `requestAnimationFrame peaked at ${stats.timers.rafPerSecPeak}/sec — likely thinking timer at 60fps`,
        fix: 'Replace rAF think timer with 1s setInterval',
      });
    }
    for (const row of stats.timers.fastIntervals.values()) {
      if (row.delay <= 100 && row.fires > 200) {
        issues.push({
          severity: 'HIGH',
          area: 'polling',
          msg: `${row.delay}ms interval fired ${row.fires}× (${row.count} timers created) — agent tool ticker or background poll`,
          fix: row.delay <= 50 ? 'Slow agent elapsed ticker to 250ms' : 'Check chat.js background stream poll (500ms)',
        });
      }
      if (row.delay === 1000 && row.count >= 3) {
        issues.push({
          severity: 'LOW',
          area: 'polling',
          msg: `${row.count} permanent 1s intervals (_syncRailDynamic, modalManager scan, etc.)`,
          fix: 'Replace with event-driven updates',
        });
      }
    }
    if (stats.observers.mutationCreated >= 8) {
      issues.push({
        severity: 'MEDIUM',
        area: 'observers',
        msg: `${stats.observers.mutationCreated} MutationObservers (${ms(stats.observers.callbackMs)} in callbacks) — body-level observers cascade`,
        fix: 'Scope observers to #chat-history / #chat-container only',
      });
    }
    if (stats.layout.forcedReflows > 50) {
      issues.push({
        severity: 'MEDIUM',
        area: 'layout',
        msg: `${stats.layout.forcedReflows} forced reflows (write→read within 16ms)`,
        fix: 'Batch DOM writes; avoid offsetWidth reads after classList changes',
      });
    }
    if (h.innerHTML.chatHistory >= 10) {
      issues.push({
        severity: 'MEDIUM',
        area: 'chat',
        msg: `#chat-history innerHTML cleared/rebuilt ${h.innerHTML.chatHistory} times`,
        fix: 'Avoid full chatHistory.innerHTML on session switch when cached',
      });
    }
    if (stats.domSnapshots.length >= 2) {
      const last = stats.domSnapshots[stats.domSnapshots.length - 1];
      if (last.bodyDropdowns > 5) {
        issues.push({
          severity: 'MEDIUM',
          area: 'sessions',
          msg: `${last.bodyDropdowns} session dropdown menus on document.body (leaked?)`,
          fix: 'Reuse one dropdown or remove on renderSessionList',
        });
      }
    }
    if (h.addMessage && h.addMessage.count > 50 && h.addMessage.totalMs / h.addMessage.count > 5) {
      issues.push({
        severity: 'MEDIUM',
        area: 'chat',
        msg: `addMessage avg ${ms(h.addMessage.totalMs / h.addMessage.count)} (${h.addMessage.count} calls) — markdown+hljs per message`,
        fix: 'Batch history render; defer hljs until stream done',
      });
    }
    const researchFetches = rankFetches().filter(r => r.url.includes('/research/'));
    const researchRate = researchFetches.reduce((s, r) => s + r.count, 0) / ((performance.now() - START) / 60000);
    if (researchRate > 20) {
      issues.push({
        severity: 'MEDIUM',
        area: 'polling',
        msg: `Research/status polling ~${researchRate.toFixed(0)}/min across overlapping timers`,
        fix: 'Unify research poll into one 5s interval',
      });
    }
    if (stats.longTasks.length >= 3) {
      const worst = [...stats.longTasks].sort((a, b) => b.dur - a.dur)[0];
      issues.push({
        severity: worst.dur > 200 ? 'HIGH' : 'MEDIUM',
        area: 'main-thread',
        msg: `${stats.longTasks.length} long tasks; worst ${ms(worst.dur)} at +${ms(worst.at)}`,
        fix: 'Profile that window in Performance tab; check selectSession + hljs + censor',
      });
    }

    return issues.sort((a, b) => {
      const rank = { HIGH: 0, MEDIUM: 1, LOW: 2 };
      return (rank[a.severity] || 9) - (rank[b.severity] || 9);
    });
  }

  function report() {
    const elapsed = performance.now() - START;
    const issues = diagnose();
    const fetches = rankFetches().slice(0, 15);

    console.group('%c Odysseus perf debug report ', 'background:#e06c75;color:#fff;font-weight:bold;padding:2px 6px');
    console.log(`Running ${ms(elapsed)} · page age ${ms(LOAD_AGE_MS)} · route ${location.pathname}`);

    console.group('Likely culprits (ranked)');
    if (!issues.length) console.log('No automatic flags yet — interact (switch sessions, type, stream) then report() again');
    issues.forEach((i, n) => console.log(`${n + 1}. [${i.severity}] ${i.area}: ${i.msg}\n   → ${i.fix}`));
    console.groupEnd();

    console.group('Hot functions');
    Object.entries(stats.hooks).forEach(([k, v]) => {
      if (typeof v === 'number') {
        if (v) console.log(`${k}: ${v}`);
        return;
      }
      if (!v.count) return;
      console.log(`${k}: ${v.count}× avg ${ms(v.totalMs / v.count)} max ${ms(v.maxMs)} total ${ms(v.totalMs)}`);
    });
    console.groupEnd();

    console.group('innerHTML rebuilds');
    console.table(stats.hooks.innerHTML);
    console.groupEnd();

    console.group('Fetch (by total time)');
    console.table(fetches.map(f => ({
      url: f.url,
      count: f.count,
      avgMs: +f.avgMs.toFixed(1),
      maxMs: +f.maxMs.toFixed(1),
      totalMs: +f.totalMs.toFixed(1),
      errors: f.errors,
    })));
    console.groupEnd();

    if (stats.fetchSlow.length) {
      console.group('Slow fetches (≥400ms)');
      console.table(stats.fetchSlow.slice(-20));
      console.groupEnd();
    }

    console.group('Timers & observers (since debug start)');
    console.log({
      intervalsCreated: stats.timers.intervalsCreated,
      timeoutsCreated: stats.timers.timeoutsCreated,
      rafCreated: stats.timers.rafCreated,
      rafPeakPerSec: stats.timers.rafPerSecPeak,
      mutationObservers: stats.observers.mutationCreated,
      observerCallbackMs: +stats.observers.callbackMs.toFixed(1),
    });
    const fast = [...stats.timers.fastIntervals.values()]
      .filter(r => r.fires > 0)
      .sort((a, b) => b.fires - a.fires);
    if (fast.length) {
      console.table(fast.map(r => ({
        delay: r.delay + 'ms',
        timersCreated: r.count,
        callbackFires: r.fires,
      })));
    }
    console.groupEnd();

    console.group('Layout');
    console.log({
      reads: stats.layout.reads,
      writes: stats.layout.writes,
      forcedReflows: stats.layout.forcedReflows,
    });
    console.groupEnd();

    const snap = domSnapshot('report');
    console.group('DOM now');
    console.log(snap);
    console.groupEnd();

    if (stats.hooks.loadSessions.traces.length) {
      console.group('loadSessions traces');
      stats.hooks.loadSessions.traces.forEach((t, i) => {
        console.log(`#${i + 1} ${ms(t.dur)} at +${ms(t.at)}`);
        console.log(t.stack);
      });
      console.groupEnd();
    }
    if (stats.hooks.selectSession.traces.length) {
      console.group('selectSession traces');
      stats.hooks.selectSession.traces.forEach((t, i) => {
        console.log(`#${i + 1} ${ms(t.dur)} at +${ms(t.at)}`);
        console.log(t.stack);
      });
      console.groupEnd();
    }

    console.group('Tips');
    console.log('1. Reproduce slowness, then run __odysseusPerfDebug.report() again');
    console.log('2. __odysseusPerfDebug.traceNext(5) then switch tabs to capture call stacks');
    console.log('3. Chrome Performance tab → record while switching session / streaming');
    console.log('4. __odysseusPerfDebug.reportHtml() for live overlay');
    console.groupEnd();

    console.groupEnd();
    return { elapsed, issues, fetches, snap };
  }

  function reportHtml() {
    if (panelEl) {
      panelEl.remove();
      panelEl = null;
      if (panelTimer) clearInterval(panelTimer);
      panelTimer = null;
      return;
    }

    panelEl = document.createElement('div');
    panelEl.id = 'odysseus-perf-debug-panel';
    panelEl.style.cssText = [
      'position:fixed', 'bottom:12px', 'left:12px', 'z-index:999999',
      'width:min(420px,calc(100vw - 24px))', 'max-height:50vh', 'overflow:auto',
      'background:#1e1e1e', 'color:#abb2bf', 'font:12px/1.4 monospace',
      'border:1px solid #e06c75', 'border-radius:8px', 'padding:10px',
      'box-shadow:0 8px 32px rgba(0,0,0,.45)',
    ].join(';');

    function render() {
      const issues = diagnose().slice(0, 6);
      const h = stats.hooks;
      const snap = domSnapshot('panel');
      const topFetch = rankFetches()[0];
      panelEl.innerHTML = [
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">',
        '<strong style="color:#e06c75">Odysseus perf</strong>',
        '<span>',
        '<button type="button" id="ody-perf-report" style="margin-right:6px;cursor:pointer">Log</button>',
        '<button type="button" id="ody-perf-close" style="cursor:pointer">×</button>',
        '</span></div>',
        `<div>⏱ ${ms(performance.now() - START)} · msgs ${snap.chatMsgs} · nodes ${snap.nodes}</div>`,
        `<div>loadSessions ${h.loadSessions.count}× (${ms(h.loadSessions.totalMs)}) · selectSession ${h.selectSession.count}× (${ms(h.selectSession.totalMs)})</div>`,
        `<div>scroll ${h.scrollHistory.count} (peak ${h.scrollHistory.peakPerSec}/s) · rAF peak ${stats.timers.rafPerSecPeak}/s</div>`,
        `<div>observers ${stats.observers.mutationCreated} · reflows ${stats.layout.forcedReflows}</div>`,
        topFetch ? `<div>top fetch: ${topFetch.url} (${topFetch.count}× ${ms(topFetch.totalMs)})</div>` : '',
        '<div style="margin-top:8px;color:#e5c07b">Flags:</div>',
        issues.length
          ? '<ul style="margin:4px 0 0;padding-left:16px">' + issues.map(i =>
            `<li style="color:${i.severity === 'HIGH' ? '#e06c75' : '#d19a66'}">[${i.severity}] ${i.msg}</li>`
          ).join('') + '</ul>'
          : '<div style="opacity:.7">Interact with IDE, flags appear here…</div>',
      ].join('');
      panelEl.querySelector('#ody-perf-close').onclick = () => api.reportHtml();
      panelEl.querySelector('#ody-perf-report').onclick = () => api.report();
    }

    document.body.appendChild(panelEl);
    render();
    panelTimer = setInterval(render, 2000);
  }

  function start() {
    if (active) return api;
    active = true;
    hookFetch();
    hookTimers();
    hookMutationObserver();
    hookLayoutReads();
    hookLongTasks();
    hookDomTargets();

    const retry = [];
    if (!hookSessionModule()) retry.push('sessionModule');
    if (!hookUiModule()) retry.push('uiModule');
    hookChatModule();
    hookWorkspaceExplorer();

    if (retry.length) {
      const id = setInterval(() => {
        if (retry.includes('sessionModule') && hookSessionModule()) retry.splice(retry.indexOf('sessionModule'), 1);
        if (retry.includes('uiModule') && hookUiModule()) retry.splice(retry.indexOf('uiModule'), 1);
        hookChatModule();
        hookDomTargets();
        if (!retry.length) clearInterval(id);
      }, 500);
    }

    domSnapshot('start');
    console.info(
      '%c[perfDebug] active%c — use __odysseusPerfDebug.report() or .reportHtml()',
      'color:#e06c75;font-weight:bold', 'color:inherit'
    );
    return api;
  }

  function stop() {
    active = false;
    if (originals.fetch) window.fetch = originals.fetch;
    if (originals.setInterval) window.setInterval = originals.setInterval;
    if (originals.setTimeout) window.setTimeout = originals.setTimeout;
    if (originals.requestAnimationFrame) window.requestAnimationFrame = originals.requestAnimationFrame;
    if (originals.MutationObserver) window.MutationObserver = originals.MutationObserver;
    if (originals.longTaskObs) originals.longTaskObs.disconnect();
    if (panelTimer) clearInterval(panelTimer);
    console.info('[perfDebug] stopped (data retained)');
    return api;
  }

  const api = {
    _running: true,
    start,
    stop,
    report,
    reportHtml,
    reset() {
      stats.longTasks.length = 0;
      stats.fetches.clear();
      stats.fetchSlow.length = 0;
      Object.values(stats.hooks).forEach(h => {
        if (typeof h === 'object' && 'count' in h) {
          h.count = 0; h.totalMs = 0; h.maxMs = 0;
          if (h.traces) h.traces.length = 0;
        }
      });
      stats.hooks.innerHTML = { chatHistory: 0, sessionList: 0, wsTree: 0, chatTabBar: 0, other: 0 };
      stats.domSnapshots.length = 0;
      console.info('[perfDebug] counters reset');
      return api;
    },
    snapshot: domSnapshot,
    traceNext(n = 3) {
      traceBudget = n * 2;
      console.info(`[perfDebug] will capture stacks for next ${n} loadSessions + ${n} selectSession calls`);
      return api;
    },
    getStats: () => stats,
    diagnose,
  };

  window.__odysseusPerfDebug = api;
  start();
})();
