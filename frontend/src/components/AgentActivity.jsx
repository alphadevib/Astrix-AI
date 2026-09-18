// Agent activity feed and the mission-learning panel.
//
// The activity feed is the demo's narration: it shows the agents acting in
// sequence, in real time, without anyone having to describe what is happening.

import { INK, SERIES, STATUS } from '../theme'
import { Empty, OutcomePill, Panel, Pill, fmt } from './primitives'

const EVENT_STYLE = {
  mission_started: { label: 'MISSION STARTED', color: SERIES[0] },
  launch_milestone: { label: 'LAUNCH', color: SERIES[3] },
  orbit_acquired: { label: 'ASTRIX ONLINE', color: STATUS.good },
  mission_stopped: { label: 'MISSION STOPPED', color: INK.muted },
  approval_expired: { label: 'APPROVAL WITHDRAWN', color: INK.muted },
  fault_injected: { label: 'FAULT INJECTED', color: STATUS.warning },
  fault_cleared: { label: 'FAULT CLEARED', color: INK.muted },
  detection: { label: 'DETECTION', color: SERIES[0] },
  suppressed: { label: 'SUPPRESSED', color: INK.muted },
  diagnosis: { label: 'DIAGNOSIS', color: SERIES[2] },
  risk: { label: 'RISK', color: SERIES[3] },
  plan: { label: 'PLAN', color: SERIES[1] },
  simulation: { label: 'SIMULATION', color: SERIES[0] },
  safety: { label: 'SAFETY', color: SERIES[2] },
  approval_required: { label: 'APPROVAL REQUIRED', color: STATUS.warning },
  approval: { label: 'APPROVAL', color: STATUS.good },
  executed: { label: 'EXECUTED', color: STATUS.good },
  command_applied: { label: 'COMMAND APPLIED', color: STATUS.good },
  outcome: { label: 'OUTCOME MEASURED', color: SERIES[2] },
  learning: { label: 'MEMORY UPDATED', color: SERIES[2] },
  anomaly_closed: { label: 'ANOMALY CLOSED', color: INK.muted },
  runner_error: { label: 'RUNNER ERROR', color: STATUS.critical },
}

// One line of human-readable detail per event type. Anything not summarised here
// simply shows its label, which is better than dumping raw JSON at an operator.
function describe(event) {
  const p = event.payload ?? {}
  switch (event.type) {
    case 'mission_started':
      return p.include_launch ? 'Countdown started from the launch pad' : 'Satellite placed directly in orbit'
    case 'launch_milestone':
      return `${p.t < 0 ? `T${p.t}` : `T+${p.t}`}s · ${p.title} — ${p.description}`
    case 'orbit_acquired':
      return 'Satellite deployed and settled; anomaly detection engaged'
    case 'approval_expired':
      return `${fmt.title(p.action_id)} — ${p.reason}`
    case 'fault_injected':
      return `${p.title} — expect ${p.expected_signals?.join(', ')}`
    case 'detection':
      return `${p.severity} · ml ${fmt.num(p.ml_score, 3)} → risk ${fmt.num(p.final_score, 3)}`
    case 'suppressed':
      return p.reason
    case 'diagnosis':
      return `${p.subsystem}${p.component ? ` / ${fmt.title(p.component)}` : ''} · ${fmt.pct(
        p.confidence,
      )} · ${p.reasoner}`
    case 'risk':
      return `mission impact ${p.mission_impact}${
        p.time_to_impact_minutes ? ` · ~${fmt.num(p.time_to_impact_minutes, 0)} min` : ''
      }`
    case 'plan':
      return `${p.options?.length ?? 0} options · recommends ${fmt.title(p.selected_action_id)}`
    case 'simulation':
      return `${p.status} · ${fmt.title(p.action_id)} · predicted benefit ${fmt.pct(
        p.effectiveness_estimate,
      )}`
    case 'safety':
      return `${p.status} · ${p.effective_risk_level} · ${fmt.title(p.approval)}`
    case 'approval_required':
      return `${fmt.title(p.action_id)} (${p.risk_level}) awaiting a flight director`
    case 'approval':
      return `${fmt.title(p.action_id)} ${p.approved ? 'approved' : 'rejected'}`
    case 'executed':
      return `${fmt.title(p.action_id)} by ${p.operator} · measuring for ${p.evaluating_for_frames} frames`
    case 'command_applied':
      return p.effect
    case 'outcome':
      return `${p.outcome} · measured ${fmt.pct(p.effectiveness)}${
        p.predicted != null ? ` (twin predicted ${fmt.pct(p.predicted)})` : ''
      }`
    case 'learning':
      return `${p.lessons?.length ?? 0} lesson(s) written to mission memory`
    case 'anomaly_closed':
      return p.reason
    default:
      return ''
  }
}

// `fill` lets a parent (the sticky rail) size the feed instead of a fixed max height.
export function AgentActivity({ activity, fill = false }) {
  return (
    <Panel
      title="Agent activity"
      note={`${activity.length} events`}
      className="scroll activity-feed"
      style={fill ? undefined : { maxHeight: 620 }}
      accent="#199e70"
    >
      {activity.length === 0 ? (
        <Empty>
          No activity yet. Start the mission to fly the launch; once Astrix is online, inject a
          fault to watch the loop run.
        </Empty>
      ) : (
        <div style={{ display: 'grid', gap: 9 }}>
          {activity.map((event, index) => {
            const style = EVENT_STYLE[event.type] ?? { label: event.type.toUpperCase(), color: INK.muted }
            const detail = describe(event)
            return (
              <div key={`${event.at}-${index}`} className="activity-item" style={{ '--event': style.color }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    color: style.color,
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 8,
                  }}
                >
                  <span>{style.label}</span>
                  <span style={{ color: INK.muted }}>{fmt.time(event.at)}</span>
                </div>
                {detail && (
                  <div className="small" style={{ color: INK.secondary, marginTop: 2, lineHeight: 1.45 }}>
                    {detail}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

export function LearningPanel({ learning, outcome }) {
  if (!learning && !outcome) {
    return (
      <Panel title="Mission learning">
        <Empty>
          Nothing learned yet this session. After a recovery executes, Astrix measures the real
          outcome over the following frames and writes what it learned back to mission memory.
        </Empty>
      </Panel>
    )
  }

  return (
    <Panel title="Mission learning" note={learning ? `reasoner: ${learning.reasoner}` : undefined}>
      {outcome && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 11 }}>
          <OutcomePill outcome={outcome.outcome} />
          <Pill color={SERIES[0]}>measured {fmt.pct(outcome.effectiveness)}</Pill>
          {outcome.predicted != null && (
            <Pill color={INK.muted}>twin predicted {fmt.pct(outcome.predicted)}</Pill>
          )}
        </div>
      )}

      {outcome && (
        <p className="small muted" style={{ margin: '0 0 11px', lineHeight: 1.55 }}>
          Health indicator {fmt.num(outcome.indicator_before, 3)} → {fmt.num(outcome.indicator_after, 3)}{' '}
          after {fmt.title(outcome.action_id)}. This is a measurement taken from telemetry after the
          fact, not the twin's prediction.
        </p>
      )}

      {learning?.lessons?.map((lesson) => (
        <div key={lesson.description} className="evidence" style={{ marginBottom: 9 }}>
          <div
            className="mono"
            style={{ fontSize: 10, color: SERIES[2], letterSpacing: '0.08em', marginBottom: 3 }}
          >
            {fmt.title(lesson.category).toUpperCase()} · {fmt.pct(lesson.confidence)} ·{' '}
            {fmt.title(lesson.validation_status)}
          </div>
          {lesson.description}
        </div>
      ))}

      {learning?.latent_patterns?.length > 0 && (
        <>
          <h3 className="panel-title" style={{ margin: '11px 0 5px' }}>
            Potential latent patterns
          </h3>
          <ul className="clean">
            {learning.latent_patterns.map((pattern) => (
              <li key={pattern}>{pattern}</li>
            ))}
          </ul>
        </>
      )}

      {learning?.unresolved_risks?.length > 0 && (
        <>
          <h3 className="panel-title" style={{ margin: '11px 0 5px' }}>
            Unresolved risks
          </h3>
          <ul className="clean">
            {learning.unresolved_risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  )
}
