import { useState } from 'react'
import MissionControl from './pages/MissionControl'
import MissionMemory from './pages/MissionMemory'
import { Pill, fmt } from './components/primitives'
import useMissionStream from './services/useMissionStream'
import { SERIES, STATUS } from './theme'

const TABS = [
  { key: 'control', label: 'Mission Control' },
  { key: 'memory', label: 'Mission Memory' },
]

export default function App() {
  const [tab, setTab] = useState('control')
  const stream = useMissionStream()
  const { connected, reasoner, pipeline, mission, latest } = stream

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <h1>ASTRIX</h1>
          <span className="tagline">Detect. Reason. Recover. Learn.</span>
        </div>

        <div className="header-right">
          <Pill color={mission?.phase === 'FAILED' ? STATUS.critical : mission?.running ? STATUS.good : '#8a8a80'}>
            {mission?.running ? fmt.title(mission.phase).toLowerCase() : mission?.phase === 'FAILED' ? 'runner failed' : 'mission stopped'}
          </Pill>
          <Pill
            color={reasoner.llm ? SERIES[0] : '#8a8a80'}
            title={reasoner.reason ?? 'LLM reasoning active'}
          >
            {reasoner.label}
          </Pill>
          <Pill color={SERIES[2]}>autonomy ≤ {pipeline?.autonomy_limit ?? '—'}</Pill>
          {pipeline?.suppressed > 0 && (
            <Pill color="#8a8a80">{pipeline.suppressed} suppressed</Pill>
          )}
          <Pill color={connected ? STATUS.good : STATUS.critical}>
            {connected ? 'live' : 'reconnecting'}
          </Pill>
        </div>
      </header>

      {!reasoner.llm && reasoner.reason && (
        <p className="small muted" style={{ margin: '-6px 4px 12px', lineHeight: 1.5 }}>
          Running on deterministic reasoners — {reasoner.reason}. Detection, the safety engine, the
          digital twin and mission memory are unaffected; only the agents' natural-language reasoning
          falls back to rules.
        </p>
      )}

      <nav className="tabs" role="tablist">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            className="tab"
            role="tab"
            aria-selected={tab === entry.key}
            onClick={() => setTab(entry.key)}
          >
            {entry.label}
          </button>
        ))}
        <span style={{ marginLeft: 'auto', alignSelf: 'center' }} className="small muted">
          {latest ? `${latest.spacecraft_id} · ${latest.mission_id} · frame ${latest.seq}` : 'no telemetry'}
        </span>
      </nav>

      {tab === 'control' ? <MissionControl stream={stream} /> : <MissionMemory />}
    </div>
  )
}
