// User-selected IDE panel layout: mobile (tab bar, 1–2 panels) or desktop (columns).
// Does not auto-switch on window resize — only changes when the user toggles.

import Storage from './storage.js';

export const IDE_LAYOUT_KEY = 'odysseus-ide-layout';
export const IDE_LAYOUT_EVENT = 'ide-layout-mode-changed';

const MODES = ['mobile', 'desktop'];

function _normalize(mode) {
  return MODES.includes(mode) ? mode : null;
}

export function getIdeLayoutMode() {
  const saved = _normalize(Storage.get(IDE_LAYOUT_KEY, ''));
  if (saved) return saved;
  const initial = window.matchMedia('(max-width: 768px)').matches ? 'mobile' : 'desktop';
  Storage.set(IDE_LAYOUT_KEY, initial);
  return initial;
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
    ? 'Mobile panel layout — click for desktop columns'
    : 'Desktop column layout — click for mobile panels';
  btn.setAttribute('aria-label', btn.title);
}

export function setIdeLayoutMode(mode) {
  const next = _normalize(mode) || 'desktop';
  const prev = getIdeLayoutMode();
  if (prev === next) return;
  Storage.set(IDE_LAYOUT_KEY, next);
  _applyBodyClasses(next);
  _updateToggleButton(next);
  document.dispatchEvent(new CustomEvent(IDE_LAYOUT_EVENT, { detail: { mode: next, prev } }));
}

export function toggleIdeLayoutMode() {
  setIdeLayoutMode(isMobileIdeLayout() ? 'desktop' : 'mobile');
}

export function initIdeLayoutMode() {
  const mode = getIdeLayoutMode();
  _applyBodyClasses(mode);
  _updateToggleButton(mode);

  const btn = document.getElementById('ide-layout-toggle');
  if (btn && btn.dataset.wired !== '1') {
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => toggleIdeLayoutMode());
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
  setIdeLayoutMode,
  toggleIdeLayoutMode,
  initIdeLayoutMode,
  IDE_LAYOUT_KEY,
  IDE_LAYOUT_EVENT,
};
