import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import AppShell from './components/AppShell'
import useMissionStream from './services/useMissionStream'
import useHardwareLink from './services/useHardwareLink'
import './index.css'
import './styles/agentic.css'

const Assistant = lazy(() => import('./pages/Assistant'))
const FlightAssurance = lazy(() => import('./pages/FlightAssurance'))
const InterceptLab = lazy(() => import('./pages/InterceptLab'))
const VehicleStudio = lazy(() => import('./pages/VehicleStudio'))
const HardwareLink = lazy(() => import('./pages/HardwareLink'))
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
    key: 'intercept',
    label: 'Intercept Lab',
    description: 'Missile trajectory testing and predicted target intercept',
    group: 'workspace',
  },
  {
    key: 'studio',
    label: 'Vehicle Studio',
    description: 'Design custom rockets and satellites, then fly them',
    group: 'workspace',
  },
  {
    key: 'hardware',
    label: 'Hardware Link',
    description: 'Arduino prototypes in the loop: live telemetry, commands and fault injection',
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

export default function Console() {
  const [page, setPage] = useState(pageFromHash)
  const [chatKey, setChatKey] = useState(0)
  const [vehicle, setVehicleState] = useState(loadVehicle)
  const stream = useMissionStream()
  const hardware = useHardwareLink()

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
    document.title = `${current.label} · Astrix-AI`
  }, [page])

  const navigate = useCallback((key) => {
    window.location.hash = `#/${key}`
  }, [])

  const newChat = useCallback(() => {
    setChatKey((k) => k + 1)
    navigate('assistant')
  }, [navigate])

  return (
    <AppShell pages={PAGES} current={page} stream={stream} hardware={hardware} onNewChat={newChat}>
      <Suspense fallback={<PageFallback />}>
        {page === 'assistant' && (
          <Assistant key={chatKey} stream={stream} vehicle={vehicle} navigate={navigate} onDesign={setVehicle} />
        )}
        {page === 'assurance' && <FlightAssurance stream={stream} vehicle={vehicle} />}
        {page === 'intercept' && <InterceptLab />}
        {page === 'studio' && <VehicleStudio design={vehicle} onDesign={setVehicle} navigate={navigate} />}
        {page === 'hardware' && <HardwareLink link={hardware} stream={stream} />}
        {page === 'model' && <ModelLab />}
        {page === 'memory' && <MissionMemory />}
      </Suspense>
    </AppShell>
  )
}
