// Viewport-driven IDE layout: desktop columns (compact when narrow) above the
// mobile breakpoint; bottom tab bar + 1–2 panels at or below it.

import Storage from './storage.js';

export const IDE_LAYOUT_KEY = 'odysseus-ide-layout';
export const IDE_LAYOUT_EVENT = 'ide-layout-mode-changed';
export const IDE_LAYOUT_SYNC_EVENT = 'ide-layout-sync';
export const MOBILE_BREAKPOINT = 768;

const MODES = ['mobile', 'desktop'];

function _normalize(mode) {
  return MODES.includes(mode) ? mode : null;
}

export function viewportPrefersMobileLayout() {
  return window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`).matches;
}

let _appliedMode = null;

/** Current layout mode — follows viewport width (<=768 mobile, >768 desktop). */
export function getIdeLayoutMode() {
  return viewportPrefersMobileLayout() ? 'mobile' : 'desktop';
}

export function isMobileIdeLayout() {
  return getIdeLayoutMode() === 'mobile';
}

export function isDesktopIdeLayout() {
  return getIdeLayoutMode() === 'desktop';
}

function _applyBodyClasses(mode) {
  document.body.classList.toggle('ide-layout-mobile', mode === 'mobile');
  document.body.classList.toggle('ide-layout-desktop', mode === 'desktop');
}

function _updateToggleButton(mode) {
  const btn = document.getElementById('ide-layout-toggle');
  if (!btn) return;
  const mobileActive = mode === 'mobile';
  btn.classList.toggle('ide-layout-toggle--mobile', mobileActive);
  btn.classList.toggle('ide-layout-toggle--desktop', !mobileActive);
  btn.setAttribute('aria-pressed', String(mobileActive));
  btn.title = mobileActive
    ? 'Mobile panel layout (auto — narrow window)'
    : 'Desktop column layout (auto — wide window)';
  btn.setAttribute('aria-label', btn.title);
}

function _applyMode(mode, { force = false } = {}) {
  const prev = _appliedMode;
  const changed = prev !== mode;
  if (!changed && !force) {
    try {
      document.dispatchEvent(new CustomEvent(IDE_LAYOUT_SYNC_EVENT));
    } catch (_) {}
    return;
  }
  _appliedMode = mode;
  _applyBodyClasses(mode);
  _updateToggleButton(mode);
  try {
    Storage.set(IDE_LAYOUT_KEY, mode);
  } catch (_) {}
  document.dispatchEvent(new CustomEvent(IDE_LAYOUT_EVENT, { detail: { mode, prev: prev ?? mode } }));
  try {
    document.dispatchEvent(new CustomEvent(IDE_LAYOUT_SYNC_EVENT));
  } catch (_) {}
}

/** Reconcile layout mode with current viewport (call on load + resize). */
export function syncIdeLayoutToViewport() {
  _applyMode(getIdeLayoutMode());
}

/** Legacy API — viewport always wins; kept for callers that set mode explicitly. */
export function setIdeLayoutMode(mode) {
  const next = _normalize(mode);
  if (!next) return;
  if (next === 'mobile' && !viewportPrefersMobileLayout()) return;
  if (next === 'desktop' && viewportPrefersMobileLayout()) return;
  _applyMode(next, { force: true });
}

export function toggleIdeLayoutMode() {
  if (viewportPrefersMobileLayout()) return;
  setIdeLayoutMode(isMobileIdeLayout() ? 'desktop' : 'mobile');
}

export function initIdeLayoutMode() {
  syncIdeLayoutToViewport();

  const btn = document.getElementById('ide-layout-toggle');
  if (btn && btn.dataset.wired !== '1') {
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => toggleIdeLayoutMode());
  }

  let timer = null;
  const onViewportChange = () => {
    clearTimeout(timer);
    timer = setTimeout(syncIdeLayoutToViewport, 80);
  };
  window.addEventListener('resize', onViewportChange);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', onViewportChange);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initIdeLayoutMode);
} else {
  initIdeLayoutMode();
}

export default {
  getIdeLayoutMode,
  isMobileIdeLayout,
  isDesktopIdeLayout,
  viewportPrefersMobileLayout,
  syncIdeLayoutToViewport,
  setIdeLayoutMode,
  toggleIdeLayoutMode,
  initIdeLayoutMode,
  IDE_LAYOUT_KEY,
  IDE_LAYOUT_EVENT,
  IDE_LAYOUT_SYNC_EVENT,
  MOBILE_BREAKPOINT,
};
