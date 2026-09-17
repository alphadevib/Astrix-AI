import { useEffect, useState } from 'react'
import AppShell from './components/AppShell'
import InterceptLab from './pages/InterceptLab'
import MissionControl from './pages/MissionControl'
import MissionMemory from './pages/MissionMemory'
import useMissionStream from './services/useMissionStream'

const PAGES = [
  {
    key: 'control',
    label: 'Mission Control',
    short: 'Control',
    description: 'Live spacecraft, the ASTRIX loop and recovery decisions',
  },
  {
    key: 'intercept',
    label: 'Intercept Check',
    short: 'Intercept',
    description: 'Trajectory check, fault and cyber-attack analysis',
  },
  {
    key: 'memory',
    label: 'Mission Memory',
    short: 'Memory',
    description: 'Knowledge profile, lessons, anomaly history and audit trail',
  },
]

// Hash routing keeps the current page across reloads and makes pages linkable
// without adding a router dependency.
function pageFromHash() {
  const key = window.location.hash.replace(/^#\/?/, '')
  return PAGES.some((p) => p.key === key) ? key : 'control'
}

export default function App() {
  const [page, setPage] = useState(pageFromHash)
  const stream = useMissionStream()

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
    document.title = `${current.label} · ASTRIX`
  }, [page])

  return (
    <AppShell pages={PAGES} current={page} stream={stream}>
      {page === 'control' && <MissionControl stream={stream} />}
      {page === 'intercept' && <InterceptLab />}
      {page === 'memory' && <MissionMemory />}
    </AppShell>
  )
}
