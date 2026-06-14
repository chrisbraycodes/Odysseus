// workspaceSessions.js — remember which chat belongs to each workspace folder.
import Storage, { KEYS } from './storage.js';

function _loadMap() {
  const map = Storage.getJSON(KEYS.WORKSPACE_SESSIONS, {});
  return map && typeof map === 'object' ? map : {};
}

function _saveMap(map) {
  Storage.setJSON(KEYS.WORKSPACE_SESSIONS, map || {});
}

export function isRestorableSession(sessionId, sessions, ctx = {}) {
  if (!sessionId) return false;
  if (ctx.isIncognito && ctx.isIncognito(sessionId)) return false;
  const meta = (sessions || []).find((s) => s.id === sessionId);
  if (!meta || meta.archived) return false;
  if (ctx.isTransient && ctx.isTransient(meta)) return false;
  return true;
}

export function bindSessionToWorkspace(workspacePath, sessionId, ctx = {}) {
  const path = (workspacePath || '').trim();
  if (!path || !sessionId) return;
  if (!isRestorableSession(sessionId, ctx.sessions, ctx)) return;
  const map = _loadMap();
  map[path] = sessionId;
  _saveMap(map);
}

export function getBoundSessionId(workspacePath) {
  const path = (workspacePath || '').trim();
  if (!path) return null;
  return _loadMap()[path] || null;
}

export function resolveRestorableSessionForWorkspace(workspacePath, sessions, ctx = {}) {
  const id = getBoundSessionId(workspacePath);
  if (!id) return null;
  if (!isRestorableSession(id, sessions, ctx)) {
    unbindWorkspace(workspacePath);
    return null;
  }
  return id;
}

export function unbindWorkspace(workspacePath) {
  const path = (workspacePath || '').trim();
  if (!path) return;
  const map = _loadMap();
  if (!map[path]) return;
  delete map[path];
  _saveMap(map);
}

export function unlinkSessionId(sessionId) {
  if (!sessionId) return;
  const map = _loadMap();
  let changed = false;
  for (const [path, sid] of Object.entries(map)) {
    if (sid === sessionId) {
      delete map[path];
      changed = true;
    }
  }
  if (changed) _saveMap(map);
}
