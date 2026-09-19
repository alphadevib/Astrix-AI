// Thin REST client for the Astrix backend.
//
// Paths are relative so the Vite dev proxy (and any reverse proxy in front of a
// build) handles routing. On Vercel the frontend and the API live on different
// origins: set VITE_API_BASE at build time, or let the operator enter the backend
// URL in the profile console (stored per browser).
//
// Every request carries the operator's session token. A 401 means the session
// is gone, so the session is cleared and the console falls back to sign-in.

import session from './auth'

const BASE_KEY = 'astrix.apiBase'

function readStorage(key) {
  try {
    return window.localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function writeStorage(key, value) {
  try {
    if (value) window.localStorage.setItem(key, value)
    else window.localStorage.removeItem(key)
  } catch {
    /* storage unavailable (private mode) — the value just won't persist */
  }
}

export function apiBase() {
  return (readStorage(BASE_KEY) || import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')
}

export const connection = {
  base: apiBase,
  saveBase(base) {
    writeStorage(BASE_KEY, (base ?? '').trim().replace(/\/$/, ''))
  },
}

const PASSWORD_CHECKS = ['/auth/login', '/auth/register', '/auth/password']

async function request(path, options = {}) {
  const token = session.token()
  const response = await fetch(`${apiBase()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  })
  if (!response.ok) {
    // A revoked or expired session: drop it so the console shows sign-in. These
    // endpoints also answer 401 for a wrong password, which is not an expiry.
    if (response.status === 401 && !PASSWORD_CHECKS.some((p) => path.startsWith(p))) {
      session.expire()
    }
    // FastAPI puts the useful part in `detail`; surface it rather than "500".
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${response.status} ${detail}`)
  }
  if (response.status === 204) return null
  const type = response.headers.get('content-type') ?? ''
  return type.includes('json') ? response.json() : response.text()
}

const get = (path) => request(path)
const send = (method) => (path, body) =>
  request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) })
const post = send('POST')
const patch = send('PATCH')
const del = send('DELETE')

export const api = {
  // --- account ---
  register: (body) => post('/auth/register', body),
  login: (email, password) => post('/auth/login', { email, password }),
  logout: () => post('/auth/logout'),
  logoutAll: () => post('/auth/logout-all'),
  me: () => get('/auth/me'),
  updateProfile: (fields) => patch('/auth/me', fields),
  changePassword: (currentPassword, newPassword, signOutOthers = true) =>
    post('/auth/password', {
      current_password: currentPassword,
      new_password: newPassword,
      sign_out_others: signOutOthers,
    }),
  sessions: () => get('/auth/sessions'),

  health: () => get('/health'),
  meta: () => get('/meta'),
  status: () => get('/status'),
  recentEvents: (limit = 50) => get(`/events/recent?limit=${limit}`),

  // --- mission simulator ---
  scenarios: () => get('/scenarios'),
  missionStatus: () => get('/mission/status'),
  startMission: (body) => post('/mission/start', body),
  stopMission: () => post('/mission/stop'),
  injectFault: (key) => post(`/mission/inject/${key}`),
  clearFault: () => post('/mission/clear-fault'),

  // --- interceptor trajectory check ---
  interceptFaults: () => get('/intercept/faults'),
  simulateIntercept: (config) => post('/intercept/simulate', config),

  // --- telemetry ---
  history: (limit = 180, spacecraftId = 'ASTRIX-01') =>
    get(`/telemetry/history?limit=${limit}&spacecraft_id=${encodeURIComponent(spacecraftId)}`),
  latest: (spacecraftId = 'ASTRIX-01') =>
    get(`/telemetry/latest?spacecraft_id=${encodeURIComponent(spacecraftId)}`),

  // --- recovery ---
  pendingApprovals: () => get('/recovery/pending'),
  approve: (anomalyId, actionId, approved, operator = 'flight-director', note = '') =>
    post('/recovery/approve', {
      anomaly_id: anomalyId,
      action_id: actionId,
      approved,
      operator,
      note,
    }),
  simulate: (actionId, frame) => post('/recovery/simulate', { action_id: actionId, frame }),

  // --- memory ---
  anomalies: (limit = 40) => get(`/anomalies?limit=${limit}`),
  profile: (spacecraftId = 'ASTRIX-01') =>
    get(`/spacecraft/${encodeURIComponent(spacecraftId)}/profile`),
  lessons: (subsystem) => get(`/lessons${subsystem ? `?subsystem=${subsystem}` : ''}`),
  searchMemory: (query, k = 5) =>
    get(`/mission-memory?query=${encodeURIComponent(query)}&k=${k}`),
  audit: (limit = 120) => get(`/audit?limit=${limit}`),
  safetyLimits: () => get('/safety/limits'),

  // --- reasoner & assistant ---
  providers: () => get('/reasoner/providers'),
  selectReasoner: (provider, model) => post('/reasoner/select', { provider, model: model || null }),
  chat: (message, conversationId = null, history = [], vehicle = null) =>
    post('/assistant/chat', { message, conversation_id: conversationId, history, vehicle }),
  conversations: () => get('/assistant/conversations'),
  conversation: (id) => get(`/assistant/conversations/${encodeURIComponent(id)}`),
  renameConversation: (id, title) => patch(`/assistant/conversations/${encodeURIComponent(id)}`, { title }),
  deleteConversation: (id) => del(`/assistant/conversations/${encodeURIComponent(id)}`),

  // --- vehicle studio ---
  vehiclePresets: () => get('/vehicles/presets'),
  analyseVehicle: (design) => post('/vehicles/analyse', design),
  generateVehicle: (prompt, useLlm = true) => post('/vehicles/generate', { prompt, use_llm: useLlm }),


  // --- astrix-lm ---
  modelStatus: () => get('/model/status'),
  trainModel: (onlyVerified = false) => post('/model/train', { only_verified: onlyVerified }),
  verifyCorpus: () => post('/model/verify'),
  exportCorpus: (onlyVerified = true) => post('/model/export', { only_verified: onlyVerified }),
}

export default api
