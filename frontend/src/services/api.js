// Thin REST client for the ASTRIX backend.
//
// Paths are relative so the Vite dev proxy (and any reverse proxy in front of a
// build) handles routing. On Vercel the frontend and the API live on different
// origins: set VITE_API_BASE at build time, or let the operator enter the backend
// URL in Settings (stored per browser).

const TOKEN_KEY = 'astrix.apiToken'
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
  token: () => readStorage(TOKEN_KEY),
  save({ base, token }) {
    writeStorage(BASE_KEY, (base ?? '').trim().replace(/\/$/, ''))
    writeStorage(TOKEN_KEY, (token ?? '').trim())
  },
}

async function request(path, options = {}) {
  const token = connection.token()
  const response = await fetch(`${apiBase()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  })
  if (!response.ok) {
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
const post = (path, body) =>
  request(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
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
  chat: (message, history = [], vehicle = null) => post('/assistant/chat', { message, history, vehicle }),

  // --- vehicle studio ---
  vehiclePresets: () => get('/vehicles/presets'),
  analyseVehicle: (design) => post('/vehicles/analyse', design),
  generateVehicle: (prompt, useLlm = true) => post('/vehicles/generate', { prompt, use_llm: useLlm }),

  // --- hardware in the loop ---
  hardwareStatus: () => get('/hardware/status'),
  hardwareTelemetry: (readings, acks = [], transport = 'web-serial') =>
    post('/hardware/telemetry', { readings, acks, transport }),
  hardwareCommand: (command, delivery = 'queue') => post('/hardware/command', { command, delivery }),
  hardwareCalibrate: () => post('/hardware/calibrate'),
  hardwareOverlay: (enabled) => post('/hardware/overlay', { enabled }),
  hardwareDisconnect: () => post('/hardware/disconnect'),

  // --- astrix-lm ---
  modelStatus: () => get('/model/status'),
  trainModel: (onlyVerified = false) => post('/model/train', { only_verified: onlyVerified }),
  verifyCorpus: () => post('/model/verify'),
  exportCorpus: (onlyVerified = true) => post('/model/export', { only_verified: onlyVerified }),
}

export default api
