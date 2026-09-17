import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies the API and the WebSocket to the FastAPI backend, so the
// frontend can use same-origin relative paths and never needs a CORS exception or
// a hard-coded host. Override the target with VITE_API_TARGET if the backend runs
// somewhere other than port 8000.
//
// Production (Vercel) builds talk to the backend directly: set VITE_API_BASE to
// its URL at build time, or enter it in the console's Settings dialog.
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
  '/meta',
  '/reasoner',
  '/assistant',
  '/vehicles',
  '/hardware',
  '/model',
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
  build: {
    target: 'es2022',
    sourcemap: false,
    cssCodeSplit: true,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // Long-lived vendor chunks: app deploys don't invalidate cached libraries.
        manualChunks: {
          charts: ['recharts'],
        },
      },
    },
  },
})
