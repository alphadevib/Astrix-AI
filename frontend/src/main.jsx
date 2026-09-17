import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import Landing from './pages/Landing'

// The landing page ships in the entry chunk; the console (charts, canvases,
// WebSocket) is a separate chunk loaded only when someone opens /app.
const Console = lazy(() => import('./Console'))

function isConsolePath() {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (path === '/app' || path.startsWith('/app/')) return true
  // Links from before the landing page existed used /#/control etc.
  return path === '' && /^#\/(control|assurance|intercept|memory)/.test(window.location.hash)
}

if (isConsolePath() && !window.location.pathname.startsWith('/app')) {
  window.history.replaceState(null, '', `/app${window.location.hash.replace('#/control', '#/assurance')}`)
}

function Loading() {
  return (
    <div className="boot" role="status" aria-live="polite">
      <img src="/astrix-emblem.svg" alt="" width="40" height="40" />
      <span>Starting console…</span>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {isConsolePath() ? (
      <Suspense fallback={<Loading />}>
        <Console />
      </Suspense>
    ) : (
      <Landing />
    )}
  </React.StrictMode>,
)
