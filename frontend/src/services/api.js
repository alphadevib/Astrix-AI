// Thin REST client for the ASTRIX backend.
//
// Paths are relative so the Vite dev proxy (and any reverse proxy in front of a
// build) handles routing. Set VITE_API_BASE only if the API lives on a different
// origin than the page.

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
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
  return response.status === 204 ? null : response.json()
}

const get = (path) => request(path)
const post = (path, body) =>
  request(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => get('/health'),
  status: () => get('/status'),
  recentEvents: (limit = 50) => get(`/events/recent?limit=${limit}`),

  // --- mission simulator ---
  scenarios: () => get('/scenarios'),
  missionStatus: () => get('/mission/status'),
  startMission: (body) => post('/mission/start', body),
  stopMission: () => post('/mission/stop'),
  injectFault: (key) => post(`/mission/inject/${key}`),
  clearFault: () => post('/mission/clear-fault'),

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

  // --- memory ---
  anomalies: (limit = 40) => get(`/anomalies?limit=${limit}`),
  profile: (spacecraftId = 'ASTRIX-01') =>
    get(`/spacecraft/${encodeURIComponent(spacecraftId)}/profile`),
  lessons: (subsystem) => get(`/lessons${subsystem ? `?subsystem=${subsystem}` : ''}`),
  searchMemory: (query, k = 5) =>
    get(`/mission-memory?query=${encodeURIComponent(query)}&k=${k}`),
  audit: (limit = 120) => get(`/audit?limit=${limit}`),
  safetyLimits: () => get('/safety/limits'),
}

export default api
