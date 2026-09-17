// The ASTRIX loop, rendered as the spine of the dashboard.
//
// This is the single most useful thing on the screen during a demo: it shows
// which stages ran for the current cycle, which one stopped the loop, and why.
// A loop that halts at "verification" because the safety engine rejected every
// option is a *success* for the architecture, and this makes that legible
// instead of looking like a dead pipeline.

import { STATUS } from '../theme'

const STAGES = [
  { key: 'detection', label: 'Detect' },
  { key: 'memory', label: 'Remember' },
  { key: 'diagnosis', label: 'Diagnose' },
  { key: 'risk', label: 'Assess' },
  { key: 'planning', label: 'Plan' },
  { key: 'simulation', label: 'Simulate' },
  { key: 'verification', label: 'Verify' },
  { key: 'execution', label: 'Recover' },
  { key: 'learning', label: 'Learn' },
]

// Which stages a cycle actually completed, derived from what the result carries.
function completion(cycle, learning) {
  if (!cycle) return {}
  return {
    detection: Boolean(cycle.detection),
    memory: Boolean(cycle.recall?.hits?.length),
    diagnosis: Boolean(cycle.diagnosis),
    risk: Boolean(cycle.risk),
    planning: Boolean(cycle.plan?.options?.length),
    simulation: Boolean(cycle.simulation),
    verification: Boolean(cycle.safety),
    execution: cycle.execution_status === 'EXECUTED',
    learning: Boolean(learning),
  }
}

export default function LoopTrail({ cycle, learning }) {
  const done = completion(cycle, learning)
  const halted = cycle?.halted_at
  const haltIndex = STAGES.findIndex((stage) => stage.key === halted)

  return (
    <div>
      <div className="loop">
        {STAGES.map((stage, index) => {
          const complete = done[stage.key]
          const isHalt = halted === stage.key
          // Stages after a halt did not run — render them as not-reached rather
          // than as failures.
          const unreached = haltIndex >= 0 && index > haltIndex
          const state = isHalt ? 'halt' : complete ? 'done' : unreached ? 'unreached' : ''
          return (
            <div key={stage.key} className={`loop-stage ${state}`} title={isHalt ? cycle?.halt_reason ?? '' : undefined}>
              {stage.label}
              <span className="state">{isHalt ? 'halted' : complete ? 'done' : unreached ? '—' : 'idle'}</span>
            </div>
          )
        })}
      </div>

      {cycle?.halt_reason && (
        <p className="small muted" style={{ margin: '9px 2px 0', lineHeight: 1.5 }}>
          <strong style={{ color: STATUS.warning, fontFamily: 'var(--mono)', fontSize: 10.5 }}>
            HALTED AT {String(halted).toUpperCase()}:
          </strong>{' '}
          {cycle.halt_reason}
        </p>
      )}
      {cycle && !cycle.halt_reason && (
        <p className="small muted" style={{ margin: '9px 2px 0' }}>
          Cycle {cycle.cycle_id} completed in {fmtMs(cycle.total_milliseconds)} ·{' '}
          {cycle.timings?.map((t) => `${t.stage} ${fmtMs(t.milliseconds)}`).join(' · ')}
        </p>
      )}
    </div>
  )
}

function fmtMs(value) {
  if (value === null || value === undefined) return '—'
  return `${Number(value).toFixed(1)}ms`
}
