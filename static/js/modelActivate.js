// static/js/modelActivate.js — spin up selected local model, stop others in container

import uiModule from './ui.js';

const API_BASE = window.location.origin;
const PROBE_INTERVAL_MS = 4000;
const PROBE_MAX_MS = 5 * 60 * 1000;

function _findCatalogItem(modelId, url, endpointId) {
  const items = window.modelsModule?.getCachedItems?.() || [];
  const normUrl = (url || '').replace(/\/+$/, '');
  return items.find((item) => {
    if (endpointId && item.endpoint_id && item.endpoint_id !== endpointId) return false;
    const itemUrl = (item.url || '').replace(/\/+$/, '');
    if (normUrl && itemUrl && itemUrl !== normUrl) return false;
    const models = (item.models || []).concat(item.models_extra || []);
    return models.includes(modelId);
  }) || null;
}

function _shouldActivate(m) {
  const item = _findCatalogItem(m.mid, m.url, m.endpointId);
  if (!item) return false;
  if (item.category === 'local') return true;
  return !!item.offline;
}

async function _waitForOnline(m) {
  const epId = m.endpointId;
  const started = Date.now();
  while (Date.now() - started < PROBE_MAX_MS) {
    await new Promise((r) => setTimeout(r, PROBE_INTERVAL_MS));
    try {
      if (epId) {
        await fetch(`${API_BASE}/api/model-endpoints/${epId}/probe`, { credentials: 'same-origin' })
          .then((r) => r.text())
          .catch(() => {});
      }
      await fetch(`${API_BASE}/api/model-endpoints/probe-local`, { credentials: 'same-origin' }).catch(() => {});
      const items = window.modelsModule?.getCachedItems?.() || [];
      const normUrl = (m.url || '').replace(/\/+$/, '');
      const hit = items.find((item) => {
        const itemUrl = (item.url || '').replace(/\/+$/, '');
        if (normUrl && itemUrl !== normUrl) return false;
        const models = (item.models || []).concat(item.models_extra || []);
        return models.includes(m.mid) && !item.offline;
      });
      if (hit) {
        if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
        return true;
      }
      if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
    } catch (_) { /* keep polling */ }
  }
  return false;
}

/**
 * Ensure a local model is the only one using container resources.
 * @returns {Promise<object|null>} activation result or null if skipped
 */
export async function activateLocalModel(m) {
  if (!m?.mid || !_shouldActivate(m)) return null;

  let data;
  try {
    const res = await fetch(`${API_BASE}/api/model/activate`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model_id: m.mid,
        endpoint_id: m.endpointId || undefined,
        endpoint_url: m.url || undefined,
      }),
    });
    data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data.detail || data.error || res.statusText;
      if (uiModule.showError) uiModule.showError(String(msg).slice(0, 240));
      return data;
    }
  } catch (e) {
    if (uiModule.showError) uiModule.showError('Could not activate model: ' + e);
    return null;
  }

  if (data.skipped) return data;

  if (data.already_online && uiModule.showToast) {
    uiModule.showToast(`${m.display || m.mid} is online`, 4000);
  }

  const stopped = (data.stopped_sessions || []).length;
  if (stopped > 0 && uiModule.showToast) {
    uiModule.showToast(`Stopped ${stopped} other model${stopped === 1 ? '' : 's'} to free resources`, 5000);
  }

  if (data.starting) {
    if (uiModule.showToast) uiModule.showToast(`Starting ${m.display || m.mid}…`, 8000);
    const online = await _waitForOnline(m);
    if (!online && uiModule.showToast) {
      uiModule.showToast('Model is still starting — check Cookbook → Running', 9000);
    }
  }

  return data;
}

export default { activateLocalModel };
