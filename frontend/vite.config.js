import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies the API and the WebSocket to the FastAPI backend, so the
// frontend can use same-origin relative paths and never needs a CORS exception or
// a hard-coded host. Override the target with VITE_API_TARGET if the backend runs
// somewhere other than port 8000.
const target = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

const apiPaths = [
  '/telemetry',
  '/pipeline',
  '/anomaly',
  '/anomalies',
  '/diagnose',
  '/resources',
  '/risk-assessment',
  '/recovery',
  '/mission-memory',
  '/mission',
  '/launch',
  '/scenarios',
  '/spacecraft',
  '/lessons',
  '/audit',
  '/safety',
  '/status',
  '/health',
  '/events',
  '/intercept',
]

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      ...Object.fromEntries(apiPaths.map((path) => [path, { target, changeOrigin: true }])),
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
})
