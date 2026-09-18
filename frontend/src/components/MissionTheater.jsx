// The demo's centre stage: the 2D mission view beside what matters in the
// current phase.
//
// During launch that is the ascent itself — altitude, speed, loads, propellant and
// the milestone checklist. In orbit it is Astrix's live analysis: current
// severity and score, which subsystem it implicates, and the fault timeline, which
// measures in spacecraft seconds how long Astrix took to detect, alarm, diagnose
// and act after a fault was injected.

import { INK, SERIES, SEVERITY_COLOR, STATUS } from '../theme'
import MissionView2D from './MissionView2D'
import { Panel, Pill, SeverityPill, fmt } from './primitives'

const LAUNCH_PHASES = new Set(['COUNTDOWN', 'ASCENT', 'ORBIT_INSERTION', 'DEPLOYMENT'])

const PHASE_LABEL = {
  IDLE: 'On the pad',
  COUNTDOWN: 'Terminal countdown',
  ASCENT: 'Powered ascent',
  ORBIT_INSERTION: 'Orbit insertion',
  DEPLOYMENT: 'Satellite deployment',
  ORBIT: 'On orbit · Astrix monitoring',
  STOPPED: 'Mission stopped',
  FAILED: 'Runner failed',
}

const SUBSYSTEMS = ['ADCS', 'POWER', 'THERMAL', 'COMMS', 'CDH']

export default function MissionTheater({ stream }) {
  const { mission, launch, launchTrack, milestones, frames, latest, detection, cycle, timeline } = stream
  const phase = mission?.phase ?? 'IDLE'
  const isLaunch = LAUNCH_PHASES.has(phase)
  const severity = detection?.suppressed ? 'NORMAL' : detection?.severity ?? 'NORMAL'

  return (
    <Panel
      title="Mission view"
      note={PHASE_LABEL[phase] ?? phase}
      className="theater"
    >
      <div className="theater-grid">
        <div className="theater-stage">
          <MissionView2D
            phase={phase}
            launch={launch}
            launchTrack={launchTrack}
            frames={frames}
            severity={severity}
            wheelsDisabled={mission?.wheels_disabled}
          />
          <PhaseBanner phase={phase} launch={launch} latest={latest} mission={mission} />
        </div>
        <div className="theater-side">
          {isLaunch || (phase === 'IDLE' && !latest) ? (
            <LaunchSide launch={launch} milestones={milestones} phase={phase} />
          ) : (
            <AnalysisSide
              detection={detection}
              cycle={cycle}
              timeline={timeline}
              latest={latest}
              mission={mission}
            />
          )}
        </div>
      </div>
    </Panel>
  )
}

function PhaseBanner({ phase, launch, latest, mission }) {
  let clock = ''
  if (LAUNCH_PHASES.has(phase) && launch) {
    const t = launch.t ?? 0
    clock = `${t < 0 ? 'T−' : 'T+'}${fmt.num(Math.abs(t), 0)} s`
  } else if (latest) {
    clock = `MET ${fmt.num(mission?.simulated_seconds ?? latest.seq, 0)} s · frame ${latest.seq}`
  }
  return (
    <div className="theater-banner">
      <span className="mono">{PHASE_LABEL[phase] ?? phase}</span>
      {clock && <span className="mono muted">{clock}</span>}
    </div>
  )
}

// ------------------------------------------------------------------ launch

function LaunchSide({ launch, milestones, phase }) {
  const reached = new Set(milestones.map((m) => m.key))
  const all = launch?.vehicle?.milestones?.length ? launch.vehicle.milestones : LAUNCH_TIMELINE
  return (
    <div className="grid" style={{ gap: 12 }}>
      <div className="mini-tiles">
        <Mini label="Altitude" value={fmt.num(launch?.altitude_km, 1)} unit="km" />
        <Mini label="Speed" value={fmt.num(launch?.speed_kms, 2)} unit="km/s" />
        <Mini label="Downrange" value={fmt.num(launch?.downrange_km, 0)} unit="km" />
        <Mini label="Accel" value={fmt.num(launch?.acceleration_g, 2)} unit="g" />
        <Mini
          label="Dyn. pressure"
          value={fmt.num(launch?.dynamic_pressure_kpa, 1)}
          unit="kPa"
          color={launch?.dynamic_pressure_kpa > 30 ? STATUS.warning : undefined}
        />
        <Mini label="Throttle" value={fmt.num(launch?.throttle_pct, 0)} unit="%" />
      </div>

      <div>
        {(launch?.stage_propellant_pct?.length ? launch.stage_propellant_pct : [launch?.stage1_propellant_pct ?? 100, launch?.stage2_propellant_pct ?? 100]).map(
          (value, i) => (
            <Bar key={i} label={`Stage ${i + 1} propellant`} value={value} color={SERIES[i % SERIES.length]} />
          ),
        )}
      </div>

      <div className="small" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Pill color={launch?.range_safety?.startsWith('LIMIT') ? STATUS.critical : STATUS.good}>
          range safety {launch?.range_safety ?? '—'}
        </Pill>
        <Pill color={INK.muted}>anomaly detection engages in orbit</Pill>
      </div>

      {launch?.vehicle?.custom && (
        <p className="small muted" style={{ margin: 0 }}>
          Flying Studio design <strong>{launch.vehicle.name}</strong> with {launch.vehicle.payload_name}.
        </p>
      )}
      <ol className="milestones">
        {all.map((m) => {
          const done = reached.has(m.key)
          return (
            <li key={m.key} className={done ? 'done' : ''}>
              <span className="mono tick">{done ? '✓' : '·'}</span>
              <span className="mono when">{m.t < 0 ? `T${Math.round(m.t)}` : `T+${Math.round(m.t)}`}</span>
              <span>{m.title}</span>
            </li>
          )
        })}
      </ol>
      {phase === 'IDLE' && (
        <p className="small muted" style={{ margin: 0, lineHeight: 1.5 }}>
          Start the mission to fly the launch. Once the satellite is deployed and settled, Astrix
          begins monitoring and faults can be injected.
        </p>
      )}
    </div>
  )
}

// Mirrors telemetry/launch.py MILESTONES; the live list only contains reached ones.
const LAUNCH_TIMELINE = [
  { key: 'countdown', t: -10, title: 'Terminal countdown' },
  { key: 'liftoff', t: 0, title: 'Liftoff' },
  { key: 'meco', t: 150, title: 'Main engine cut-off' },
  { key: 'stage_separation', t: 153, title: 'Stage separation' },
  { key: 'stage2_ignition', t: 160, title: 'Stage 2 ignition' },
  { key: 'fairing_separation', t: 205, title: 'Fairing separation' },
  { key: 'orbit_insertion', t: 520, title: 'Orbit insertion (500 km)' },
  { key: 'payload_separation', t: 560, title: 'Payload separation' },
  { key: 'arrays_deployed', t: 590, title: 'Solar arrays deployed' },
  { key: 'detumble_complete', t: 620, title: 'Detumble complete → Astrix online' },
]

// ------------------------------------------------------------------- orbit

function AnalysisSide({ detection, cycle, timeline, latest, mission }) {
  const implicated =
    detection?.is_anomaly && !detection?.suppressed ? cycle?.diagnosis?.subsystem : null
  const color = SEVERITY_COLOR[detection?.severity] ?? STATUS.good

  return (
    <div className="grid" style={{ gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <SeverityPill severity={detection?.severity ?? 'NORMAL'} suppressed={detection?.suppressed} />
        <span className="mono small muted">
          risk {fmt.num(detection?.final_score, 2)} · ml {fmt.num(detection?.ml_score, 2)}
        </span>
      </div>
      <div className="score-track" aria-label="context-adjusted risk score">
        <div
          className="score-fill"
          style={{ width: `${Math.round((detection?.final_score ?? 0) * 100)}%`, background: color }}
        />
        {[0.35, 0.55, 0.75].map((cut) => (
          <span key={cut} className="score-cut" style={{ left: `${cut * 100}%` }} />
        ))}
      </div>

      <div className="subsystems">
        {SUBSYSTEMS.map((name) => {
          const hit = implicated === name
          return (
            <span key={name} className="subsystem" style={{ borderColor: hit ? color : undefined }}>
              <span className="dot" style={{ background: hit ? color : STATUS.good }} />
              {name}
            </span>
          )
        })}
      </div>

      <FaultTimeline timeline={timeline} mission={mission} />

      {latest && (
        <p className="small muted" style={{ margin: 0 }}>
          {latest.in_eclipse ? 'In eclipse' : 'Sunlit'} · {fmt.title(latest.mode).toLowerCase()} ·{' '}
          {latest.ground_contact ? 'ground contact' : 'no contact'}
        </p>
      )}
    </div>
  )
}

const STEPS = [
  ['detected', 'First detection'],
  ['alarm', 'Alarm (WARNING+)'],
  ['diagnosed', 'Diagnosis'],
  ['decided', 'Recovery decided'],
  ['executed', 'Command executed'],
  ['outcome', 'Outcome measured'],
]

function FaultTimeline({ timeline, mission }) {
  if (!timeline) {
    return (
      <div className="timeline empty-timeline small muted">
        No fault injected yet. Let the nominal orbit run to show Astrix staying quiet through eclipse,
        imaging passes and ground contacts, then inject a fault.
      </div>
    )
  }
  const start = timeline.injected?.seq ?? 0
  const since = (mark) => (mark?.seq != null ? `+${Math.max(0, mark.seq - start)} s` : '')

  return (
    <div className="timeline">
      <div className="timeline-head">
        <span className="mono small" style={{ color: timeline.benign ? INK.secondary : STATUS.warning }}>
          {timeline.benign ? 'BENIGN TEST' : 'INJECTED'} · {timeline.title}
        </span>
        <span className="mono small muted">frame {start}</span>
      </div>
      {timeline.benign ? (
        <BenignVerdict timeline={timeline} />
      ) : (
        <ol className="timeline-steps">
          {STEPS.map(([key, label]) => {
            const mark = timeline[key]
            return (
              <li key={key} className={mark ? 'done' : ''}>
                <span className="mono when">{mark ? since(mark) : '…'}</span>
                <span>{label}</span>
                <span className="small muted detail">{detailFor(key, mark)}</span>
              </li>
            )
          })}
        </ol>
      )}
      {!mission?.active_scenario && timeline.outcome == null && (
        <p className="small muted" style={{ margin: '6px 0 0' }}>
          fault no longer active on the spacecraft
        </p>
      )}
    </div>
  )
}

function BenignVerdict({ timeline }) {
  if (timeline.alarm) {
    return (
      <p className="small" style={{ color: STATUS.critical, margin: 0 }}>
        False alarm raised at {`+${timeline.alarm.seq - timeline.injected.seq} s`} — the benign
        transient was not suppressed.
      </p>
    )
  }
  if (timeline.suppressed) {
    return (
      <p className="small" style={{ color: STATUS.good, margin: 0 }}>
        Deviation noticed and suppressed as benign: {timeline.suppressed.reason}
      </p>
    )
  }
  return (
    <p className="small" style={{ color: STATUS.good, margin: 0 }}>
      No alarm so far — Astrix is treating the excursion as explained by context.
    </p>
  )
}

function detailFor(key, mark) {
  if (!mark) return ''
  switch (key) {
    case 'detected':
    case 'alarm':
      return mark.severity
    case 'diagnosed':
      return mark.subsystem
    case 'decided':
      return `${fmt.title(mark.action)} · ${mark.mode}`
    case 'executed':
      return mark.operator
    case 'outcome':
      return `${mark.outcome} · ${fmt.pct(mark.effectiveness)}`
    default:
      return ''
  }
}

// ---------------------------------------------------------------- helpers

function Mini({ label, value, unit, color }) {
  return (
    <div className="mini">
      <div className="mini-label">{label}</div>
      <div className="mini-value mono" style={color ? { color } : undefined}>
        {value}
        {unit && <span className="mini-unit">{unit}</span>}
      </div>
    </div>
  )
}

function Bar({ label, value, color }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div className="small muted" style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>{label}</span>
        <span className="mono">{fmt.num(value, 0)}%</span>
      </div>
      <div className="score-track" style={{ height: 6 }}>
        <div className="score-fill" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }} />
      </div>
    </div>
  )
}
