import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import AppShell from './components/AppShell'
import Auth from './pages/Auth'
import api from './services/api'
import session from './services/auth'
import useMissionStream from './services/useMissionStream'
import './index.css'
import './styles/agentic.css'
// Last, so its tokens re-point the older sheets' variables.
import './styles/astrix.css'

const Assistant = lazy(() => import('./pages/Assistant'))
const FlightAssurance = lazy(() => import('./pages/FlightAssurance'))
const VehicleStudio = lazy(() => import('./pages/VehicleStudio'))
const ModelLab = lazy(() => import('./pages/ModelLab'))
const MissionMemory = lazy(() => import('./pages/MissionMemory'))

export const PAGES = [
  {
    key: 'assistant',
    label: 'Astrix',
    description: 'Operate every lab in plain language',
    group: 'workspace',
    narrow: true,
  },
  {
    key: 'assurance',
    label: 'Flight Assurance',
    description: 'Spacecraft anomaly testing from the launch pad to orbit',
    group: 'workspace',
  },
  {
    key: 'studio',
    label: 'Vehicle Studio',
    description: 'Design custom rockets and satellites, then fly them',
    group: 'workspace',
  },
  {
    key: 'model',
    label: 'Astrix-LM',
    description: "Secure training corpus and Astrix's own models",
    group: 'intelligence',
  },
  {
    key: 'memory',
    label: 'Mission Memory',
    description: 'Knowledge profile, lessons, anomaly history and audit trail',
    group: 'intelligence',
  },
]

const ALIASES = { control: 'assurance' }

// Hash routing keeps the current page across reloads and makes pages linkable
// without adding a router dependency.
function pageFromHash() {
  const raw = window.location.hash.replace(/^#\/?/, '').split('?')[0]
  const key = ALIASES[raw] ?? raw
  return PAGES.some((p) => p.key === key) ? key : 'assistant'
}

const VEHICLE_KEY = 'astrix.studioDesign'

function loadVehicle() {
  try {
    return JSON.parse(window.localStorage.getItem(VEHICLE_KEY) ?? 'null')
  } catch {
    return null
  }
}

function PageFallback() {
  return (
    <div className="page-loading" role="status">
      <span className="spinner" aria-hidden="true" /> Loading…
    </div>
  )
}

const THREAD_KEY = 'astrix.activeThread'

function readThread() {
  try {
    return window.sessionStorage.getItem(THREAD_KEY) || null
  } catch {
    return null
  }
}

function writeThread(id) {
  try {
    if (id) window.sessionStorage.setItem(THREAD_KEY, id)
    else window.sessionStorage.removeItem(THREAD_KEY)
  } catch {
    /* storage unavailable */
  }
}

// The account gate. Nothing below it — not even the telemetry socket — starts
// until there is a session the server accepts.
export default function Console() {
  const [token, setToken] = useState(session.token)
  const [user, setUser] = useState(null)
  const [reason, setReason] = useState('')

  useEffect(
    () =>
      session.subscribe((next) => {
        setToken(next)
        if (!next) {
          setUser(null)
          writeThread(null)
        }
      }),
    [],
  )

  useEffect(() => {
    if (!token || user) return undefined
    let cancelled = false
    api
      .me()
      .then((result) => !cancelled && setUser(result.user))
      .catch((error) => {
        if (cancelled) return
        // A 401 already cleared the session; anything else means the backend is down.
        if (!/^401/.test(error.message)) setReason('The backend is unreachable. Sign in again once it is running.')
        session.set('')
      })
    return () => {
      cancelled = true
    }
  }, [token, user])

  useEffect(() => {
    if (!token) document.title = 'Sign in · Astrix'
  }, [token])

  if (!token) {
    return (
      <Auth
        reason={reason}
        onSignedIn={(signedIn) => {
          setReason('')
          setUser(signedIn)
        }}
      />
    )
  }
  if (!user) {
    return (
      <div className="boot" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span>Signing in…</span>
      </div>
    )
  }
  return <Workspace user={user} onUserUpdated={setUser} />
}

function Workspace({ user, onUserUpdated }) {
  const [page, setPage] = useState(pageFromHash)
  const [vehicle, setVehicleState] = useState(loadVehicle)
  const [threads, setThreads] = useState(null)
  const [activeThread, setActiveThread] = useState(readThread)
  // Bumped only when the operator switches or starts a thread, so the chat
  // remounts then — and not when the server assigns an id to a new one.
  const [chatKey, setChatKey] = useState(0)
  const stream = useMissionStream()

  const setVehicle = useCallback((design) => {
    setVehicleState(design)
    try {
      if (design) window.localStorage.setItem(VEHICLE_KEY, JSON.stringify(design))
      else window.localStorage.removeItem(VEHICLE_KEY)
    } catch {
      /* storage unavailable */
    }
  }, [])

  useEffect(() => {
    const onHash = () => {
      setPage(pageFromHash())
      window.scrollTo({ top: 0 })
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    const current = PAGES.find((p) => p.key === page)
    document.title = `${current.label} · Astrix`
  }, [page])

  const refreshThreads = useCallback(() => {
    api
      .conversations()
      .then((result) => setThreads(result.conversations))
      .catch(() => setThreads((list) => list ?? []))
  }, [])

  useEffect(refreshThreads, [refreshThreads])

  const navigate = useCallback((key) => {
    window.location.hash = `#/${key}`
  }, [])

  const openThread = useCallback(
    (id) => {
      setActiveThread(id)
      writeThread(id)
      setChatKey((k) => k + 1)
      navigate('assistant')
    },
    [navigate],
  )

  const newChat = useCallback(() => openThread(null), [openThread])

  const threadCreated = useCallback(
    (id) => {
      setActiveThread(id)
      writeThread(id)
      refreshThreads()
    },
    [refreshThreads],
  )

  const deleteThread = useCallback(
    async (id) => {
      const thread = threads?.find((t) => t.id === id)
      if (!window.confirm(`Delete “${thread?.title ?? 'this conversation'}”? This cannot be undone.`)) return
      setThreads((list) => list?.filter((t) => t.id !== id) ?? list)
      if (id === activeThread) newChat()
      try {
        await api.deleteConversation(id)
      } finally {
        refreshThreads()
      }
    },
    [threads, activeThread, newChat, refreshThreads],
  )

  const signOut = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* the session is dropped locally either way */
    }
    session.set('')
  }, [])

  return (
    <AppShell
      pages={PAGES}
      current={page}
      stream={stream}
      user={user}
      onUserUpdated={onUserUpdated}
      onSignOut={signOut}
      threads={threads}
      activeThread={page === 'assistant' ? activeThread : null}
      onSelectThread={openThread}
      onDeleteThread={deleteThread}
      onNewChat={newChat}
    >
      <Suspense fallback={<PageFallback />}>
        {page === 'assistant' && (
          <Assistant
            key={chatKey}
            conversationId={activeThread}
            onThreadCreated={threadCreated}
            onTurn={refreshThreads}
            onMissing={newChat}
            stream={stream}
            vehicle={vehicle}
            navigate={navigate}
            onDesign={setVehicle}
          />
        )}
        {page === 'assurance' && <FlightAssurance stream={stream} vehicle={vehicle} />}
        {page === 'studio' && <VehicleStudio design={vehicle} onDesign={setVehicle} navigate={navigate} />}
        {page === 'model' && <ModelLab />}
        {page === 'memory' && <MissionMemory />}
      </Suspense>
    </AppShell>
  )
}
