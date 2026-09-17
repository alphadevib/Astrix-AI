// Interceptor trajectory check.
//
// Before launch ASTRIX flies the engagement once with a healthy vehicle — that
// prediction is the trajectory check (GO / NO-GO) and the reference corridor.
// The operator then injects a vehicle fault or a cyber attack and plays the
// engagement back, watching what the ground-side health monitor sees, when each
// consistency check trips, and what ASTRIX attributes the anomaly to.
//
// Everything on this page is driven by one stateless API call; the playhead is
// purely client-side, so scrubbing and replaying never touch the backend.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api from '../services/api'
import InterceptView2D, { sampleAt, worstLevel } from '../components/InterceptView2D'
import { Empty, Panel, Pill, fmt } from '../components/primitives'
import { INK, SERIES, SEVERITY_COLOR, STATUS } from '../theme'

const DEFAULT_GEOMETRY = {
  target_x_km: 150,
  target_y_km: 70,
  target_vx_kms: -2,
  target_vy_kms: -0.5,
}

const DEFAULT_CONFIG = {
  ...DEFAULT_GEOMETRY,
  target_weave_kms2: 0.01,
  launch_delay_s: 4,
  fault: 'gnss_spoofing',
  onset_s: 12,
  severity: 0.6,
  link_authentication: true,
  seed: 7,
}

const DEFAULT_LAYERS = { corridor: true, truth: true, radar: true, reported: true, estimate: true }
const SPEEDS = [1, 2, 5, 10]
const CATEGORY_COLOR = { PHYSICAL: SERIES[3], CYBER: SERIES[0], UNKNOWN: INK.muted }

export default function InterceptLab() {
  const [catalog, setCatalog] = useState(null)
  const [config, setConfig] = useState(DEFAULT_CONFIG)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(2)
  const [layers, setLayers] = useState(DEFAULT_LAYERS)

  useEffect(() => {
    api.interceptFaults().then(setCatalog).catch((err) => setError(err.message))
  }, [])

  // Re-simulate whenever the configuration changes; sliders fire rapidly, so debounce.
  const request = useRef(0)
  useEffect(() => {
    const id = ++request.current
    setLoading(true)
    const timer = setTimeout(() => {
      api
        .simulateIntercept(config)
        .then((data) => {
          if (id !== request.current) return
          setResult(data)
          setError(null)
        })
        .catch((err) => id === request.current && setError(err.message))
        .finally(() => id === request.current && setLoading(false))
    }, 220)
    return () => clearTimeout(timer)
  }, [config])

  const duration = result?.samples.length ? result.samples[result.samples.length - 1].t : 0
  useEffect(() => {
    setTime((t) => Math.min(t, duration))
  }, [duration])

  // Playback clock.
  useEffect(() => {
    if (!playing) return undefined
    let id
    let last = performance.now()
    const tick = (now) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      setTime((t) => {
        const next = t + dt * speed
        if (next >= duration) {
          setPlaying(false)
          return duration
        }
        return next
      })
      id = requestAnimationFrame(tick)
    }
    id = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(id)
  }, [playing, speed, duration])

  const seek = useCallback(
    (t) => {
      setTime(Math.max(0, Math.min(duration, t)))
    },
    [duration],
  )

  // Read through refs so the keyboard listener isn't re-bound on every playback frame.
  const clock = useRef({ time, playing, duration })
  clock.current = { time, playing, duration }
  const togglePlay = useCallback(() => {
    const { time: t, playing: on, duration: end } = clock.current
    if (!on && t >= end - 1e-6) setTime(0)
    setPlaying(!on)
  }, [])

  // Keyboard: space play/pause, ←/→ step 0.2 s (shift: 2 s), Home/End.
  useEffect(() => {
    const onKey = (event) => {
      if (event.target.closest?.('input, select, textarea')) return
      if (event.code === 'Space') {
        event.preventDefault()
        togglePlay()
      } else if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault()
        setPlaying(false)
        const step = (event.shiftKey ? 2 : 0.2) * (event.key === 'ArrowRight' ? 1 : -1)
        setTime((t) => Math.max(0, Math.min(duration, t + step)))
      } else if (event.key === 'Home') seek(0)
      else if (event.key === 'End') seek(duration)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [togglePlay, seek, duration])

  const update = (patch) => setConfig((current) => ({ ...current, ...patch }))
  const checkLabels = useMemo(
    () => Object.fromEntries((result?.checks ?? catalog?.checks ?? []).map((c) => [c.key, c.label])),
    [result, catalog],
  )
  const current = sampleAt(result?.samples, time)
  const fault = catalog?.faults.find((f) => f.key === config.fault)

  return (
    <div className="grid" style={{ gap: 14 }}>
      {error && (
        <div className="banner" style={{ borderColor: STATUS.critical }}>
          <div className="banner-text">{error}</div>
        </div>
      )}

      <Panel
        title="Interceptor trajectory check"
        note="Predict the engagement, inject a fault or cyber attack, watch what the ground can see"
      >
        <Controls config={config} catalog={catalog} update={update} loading={loading} />
        {fault && (
          <div className="evidence" style={{ marginTop: 12 }}>
            <span className="mono small" style={{ color: CATEGORY_COLOR[fault.category] }}>
              {fault.category}
            </span>{' '}
            <strong className="bright">{fault.title}.</strong> {fault.description}
            <div className="small muted" style={{ marginTop: 4 }}>
              Expected signature: {fault.signature}
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Engagement view" note={`T+${fmt.num(time, 1)} s`} className="theater">
        <div className="theater-grid">
          <div>
            <div className="theater-stage">
              <InterceptView2D
                result={result}
                config={config}
                time={time}
                layers={layers}
                onToggleLayer={(key) => setLayers((l) => ({ ...l, [key]: !l[key] }))}
                onSeek={(t) => {
                  setPlaying(false)
                  seek(t)
                }}
                onTargetChange={update}
                checkLabels={checkLabels}
              />
              <div className="theater-banner">
                <span className="mono">
                  {current?.launched ? `Interceptor in flight · ${worstLevel(current.checks)}` : 'Interceptor on the pad'}
                </span>
                <span className="mono muted">drag the orange handles to re-plan · click the track to seek</span>
              </div>
            </div>
            <Playback
              result={result}
              time={time}
              duration={duration}
              playing={playing}
              speed={speed}
              onToggle={togglePlay}
              onSpeed={setSpeed}
              onSeek={(t) => {
                setPlaying(false)
                seek(t)
              }}
            />
          </div>
          <div className="theater-side grid" style={{ gap: 12, alignContent: 'start' }}>
            <Preflight preflight={result?.preflight} />
            <Outcome result={result} time={time} onSeek={seek} />
            <Readout sample={current} />
          </div>
        </div>
      </Panel>

      <Panel title="Health monitor" note="What the ground sees — debounced consistency checks, never truth">
        <CheckStrips
          result={result}
          time={time}
          onSeek={(t) => {
            setPlaying(false)
            seek(t)
          }}
        />
      </Panel>

      <div className="grid cols-2">
        <DiagnosisPanel result={result} time={time} onSeek={seek} />
        <AlertLog result={result} time={time} labels={checkLabels} onSeek={seek} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- controls

function Controls({ config, catalog, update, loading }) {
  const groups = ['PHYSICAL', 'CYBER'].map((category) => [
    category,
    (catalog?.faults ?? []).filter((f) => f.category === category),
  ])
  const linkFault = ['command_injection', 'uplink_jamming', 'telemetry_replay'].includes(config.fault)

  return (
    <div className="control-grid">
      <label className="field stacked">
        Anomaly
        <select value={config.fault ?? ''} onChange={(e) => update({ fault: e.target.value || null })}>
          <option value="">None — nominal engagement</option>
          {groups.map(([category, faults]) => (
            <optgroup key={category} label={category === 'CYBER' ? 'Cyber attacks' : 'Vehicle faults'}>
              {faults.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.title}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      <Slider
        label="Severity"
        value={config.severity}
        min={0.1}
        max={1}
        step={0.05}
        display={fmt.pct(config.severity)}
        disabled={!config.fault}
        onChange={(v) => update({ severity: v })}
      />
      <Slider
        label="Onset"
        value={config.onset_s}
        min={0}
        max={40}
        step={0.5}
        display={`T+${fmt.num(config.onset_s, 1)} s`}
        disabled={!config.fault}
        onChange={(v) => update({ onset_s: v })}
      />
      <Slider
        label="Launch delay"
        value={config.launch_delay_s}
        min={0}
        max={20}
        step={0.5}
        display={`${fmt.num(config.launch_delay_s, 1)} s`}
        onChange={(v) => update({ launch_delay_s: v })}
      />
      <Slider
        label="Target weave"
        value={config.target_weave_kms2}
        min={0}
        max={0.08}
        step={0.005}
        display={`${fmt.num(config.target_weave_kms2 / 0.00981, 1)} g`}
        onChange={(v) => update({ target_weave_kms2: v })}
      />
      <label className="field stacked">
        Seed
        <input
          type="number"
          min={0}
          value={config.seed}
          onChange={(e) => update({ seed: Math.max(0, Number(e.target.value) || 0) })}
          style={{ width: 90 }}
        />
      </label>
      <label className={`toggle ${linkFault ? 'emphasis' : ''}`}>
        <input
          type="checkbox"
          checked={config.link_authentication}
          onChange={(e) => update({ link_authentication: e.target.checked })}
        />
        uplink authentication (MAC)
      </label>
      <div className="control-row" style={{ gridColumn: 'span 2' }}>
        <button type="button" className="btn" onClick={() => update(DEFAULT_GEOMETRY)}>
          reset geometry
        </button>
        <button type="button" className="btn" onClick={() => update({ seed: Math.floor(Math.random() * 10000) })}>
          new seed
        </button>
        {loading && <span className="small muted mono">simulating…</span>}
      </div>
    </div>
  )
}

function Slider({ label, value, min, max, step, display, onChange, disabled }) {
  return (
    <label className={`field stacked ${disabled ? 'disabled' : ''}`}>
      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        {label}
        <span className="bright">{display}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  )
}

// ---------------------------------------------------------------- playback

function Playback({ result, time, duration, playing, speed, onToggle, onSpeed, onSeek }) {
  const trackRef = useRef(null)
  const onset = result?.fault ? result.config.onset_s : null
  const pct = (t) => `${duration ? (t / duration) * 100 : 0}%`

  const seekFromPointer = (event) => {
    const rect = trackRef.current.getBoundingClientRect()
    onSeek(((event.clientX - rect.left) / rect.width) * duration)
  }

  return (
    <div className="playback">
      <button type="button" className="btn" onClick={onToggle} disabled={!result} style={{ minWidth: 74 }}>
        {playing ? 'pause' : time >= duration && duration ? 'replay' : 'play'}
      </button>
      <div
        className="scrub"
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Engagement time"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={Number(time.toFixed(1))}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId)
          seekFromPointer(event)
        }}
        onPointerMove={(event) => event.buttons === 1 && seekFromPointer(event)}
      >
        <div className="scrub-fill" style={{ width: pct(time) }} />
        {result && (
          <span className="scrub-mark launch" style={{ left: pct(result.config.launch_delay_s) }} title="Launch" />
        )}
        {onset != null && onset <= duration && (
          <span className="scrub-mark onset" style={{ left: pct(onset) }} title={`Anomaly onset T+${onset} s`} />
        )}
        {result?.events.map((event) => (
          <span
            key={`${event.check}-${event.t}`}
            className="scrub-event"
            style={{ left: pct(event.t), background: SEVERITY_COLOR[event.level] }}
            title={`${event.level} ${event.check} T+${event.t} s`}
          />
        ))}
        {result?.outcome.t != null && (
          <span
            className="scrub-mark outcome"
            style={{
              left: pct(result.outcome.t),
              background: result.outcome.result === 'INTERCEPT' ? STATUS.good : STATUS.critical,
            }}
            title={`${result.outcome.result} T+${result.outcome.t} s`}
          />
        )}
        <div className="scrub-head" style={{ left: pct(time) }} />
      </div>
      <span className="mono small" style={{ minWidth: 96, textAlign: 'right' }}>
        {fmt.num(time, 1)} / {fmt.num(duration, 1)} s
      </span>
      <div className="speed-group" role="group" aria-label="Playback speed">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            className={`chip ${speed === s ? 'on' : ''}`}
            aria-pressed={speed === s}
            onClick={() => onSpeed(s)}
          >
            {s}×
          </button>
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------- side column

function Preflight({ preflight }) {
  if (!preflight) return <Empty>Running the trajectory check…</Empty>
  return (
    <div className="timeline">
      <div className="timeline-head">
        <span className="mono small">PRE-LAUNCH TRAJECTORY CHECK</span>
        <Pill color={preflight.go ? STATUS.good : STATUS.critical}>{preflight.go ? 'GO' : 'NO-GO'}</Pill>
      </div>
      <ul className="preflight">
        {preflight.checks.map((c) => (
          <li key={c.key} className={c.pass ? '' : 'fail'}>
            <span className="mono tick" style={{ color: c.pass ? STATUS.good : STATUS.critical }}>
              {c.pass ? '✓' : '✗'}
            </span>
            <span>{c.label}</span>
            <span className="mono small value" title={c.limit}>
              {c.value}
            </span>
          </li>
        ))}
      </ul>
      <p className="small muted" style={{ margin: '6px 0 0' }}>
        Healthy-vehicle prediction; it is also the corridor the flight is checked against.
      </p>
    </div>
  )
}

function Outcome({ result, time, onSeek }) {
  if (!result) return null
  const { outcome, detection } = result
  const revealed = outcome.t != null && time >= outcome.t
  const hit = outcome.result === 'INTERCEPT'
  return (
    <div className="mini-tiles">
      <button
        type="button"
        className="mini clickable"
        onClick={() => outcome.t != null && onSeek(outcome.t)}
        title="Jump to closest approach"
      >
        <div className="mini-label">Outcome</div>
        <div className="mini-value mono" style={{ color: revealed ? (hit ? STATUS.good : STATUS.critical) : INK.muted }}>
          {revealed ? outcome.result : '…'}
        </div>
      </button>
      <div className="mini">
        <div className="mini-label">Miss</div>
        <div className="mini-value mono">
          {revealed ? fmt.num(outcome.miss_km * 1000, 0) : '—'}
          <span className="mini-unit">m</span>
        </div>
      </div>
      <button
        type="button"
        className="mini clickable"
        onClick={() => detection?.first_alert_s != null && onSeek(detection.first_alert_s)}
        title="Jump to first alert"
      >
        <div className="mini-label">Detect latency</div>
        <div className="mini-value mono">
          {detection?.latency_s != null ? fmt.num(detection.latency_s, 1) : '—'}
          <span className="mini-unit">s</span>
        </div>
      </button>
    </div>
  )
}

function Readout({ sample }) {
  if (!sample) return null
  const navError = Math.hypot(sample.reported[0] - sample.radar[0], sample.reported[1] - sample.radar[1])
  const truthError = Math.hypot(sample.truth[0] - sample.radar[0], sample.truth[1] - sample.radar[1])
  return (
    <div className="mini-tiles">
      <Mini label="Altitude" value={fmt.num(sample.radar[1], 1)} unit="km" />
      <Mini label="Downrange" value={fmt.num(sample.radar[0], 1)} unit="km" />
      <Mini label="Speed" value={fmt.num(sample.radar_speed, 2)} unit="km/s" />
      <Mini label="Pred. miss" value={sample.zem != null ? fmt.num(sample.zem, 2) : '—'} unit="km" />
      <Mini
        label="Nav error"
        value={fmt.num(navError, 2)}
        unit="km"
        color={navError > 0.3 ? STATUS.warning : undefined}
        title={`radar-vs-truth noise ${fmt.num(truthError, 3)} km`}
      />
      <Mini
        label="Pkt loss"
        value={fmt.num(sample.packet_loss * 100, 0)}
        unit="%"
        color={sample.packet_loss > 0.2 ? STATUS.warning : undefined}
      />
    </div>
  )
}

function Mini({ label, value, unit, color, title }) {
  return (
    <div className="mini" title={title}>
      <div className="mini-label">{label}</div>
      <div className="mini-value mono" style={color ? { color } : undefined}>
        {value}
        {unit && <span className="mini-unit">{unit}</span>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------ check strips

function CheckStrips({ result, time, onSeek }) {
  if (!result) return <Empty>Waiting for the simulation…</Empty>
  const { samples, checks, events } = result
  const duration = samples[samples.length - 1].t || 1
  const current = sampleAt(samples, time)
  const firstTrip = Object.fromEntries(
    checks.map((c) => [c.key, events.find((e) => e.check === c.key)]),
  )

  return (
    <div className="strips">
      {checks.map((check) => {
        const segments = runs(samples, check.key)
        const level = current?.checks?.[check.key] ?? 'NOMINAL'
        const trip = firstTrip[check.key]
        return (
          <div key={check.key} className="strip-row" title={check.description}>
            <span className="strip-label">
              <span className="dot" style={{ background: SEVERITY_COLOR[level] }} />
              {check.label}
            </span>
            <div
              className="strip"
              onPointerDown={(event) => {
                const rect = event.currentTarget.getBoundingClientRect()
                onSeek(((event.clientX - rect.left) / rect.width) * duration)
              }}
            >
              {segments.map((seg) => (
                <span
                  key={seg.from}
                  className="strip-seg"
                  style={{
                    left: `${(seg.from / duration) * 100}%`,
                    width: `${Math.max(0.3, ((seg.to - seg.from) / duration) * 100)}%`,
                    background: SEVERITY_COLOR[seg.level],
                    opacity: seg.level === 'WATCH' ? 0.35 : 0.85,
                  }}
                />
              ))}
              <span className="strip-head" style={{ left: `${(time / duration) * 100}%` }} />
            </div>
            <button
              type="button"
              className="strip-trip mono small"
              disabled={!trip}
              onClick={() => trip && onSeek(trip.t)}
              title={trip ? 'Jump to first trip' : 'Never tripped'}
            >
              {trip ? `T+${fmt.num(trip.t, 1)}` : '—'}
            </button>
          </div>
        )
      })}
      <div className="small muted" style={{ marginTop: 6 }}>
        Click a strip to seek. Grey = watch, amber = warning, red = critical.
      </div>
    </div>
  )
}

// Collapse per-sample levels into [from, to) runs of non-nominal level.
function runs(samples, key) {
  const out = []
  let open = null
  for (const s of samples) {
    const level = s.checks?.[key] ?? 'NOMINAL'
    if (open && open.level !== level) {
      open.to = s.t
      if (open.level !== 'NOMINAL') out.push(open)
      open = null
    }
    if (!open) open = { from: s.t, to: s.t, level }
  }
  if (open && open.level !== 'NOMINAL') {
    open.to = samples[samples.length - 1].t + 0.2
    out.push(open)
  }
  return out
}

// -------------------------------------------------------- diagnosis + log

function DiagnosisPanel({ result, time, onSeek }) {
  const items = result?.diagnosis ?? []
  const shown = items.filter((d) => d.t != null && d.t <= time)
  const pending = items.length - shown.length
  const injected = result?.fault

  return (
    <Panel title="ASTRIX diagnosis" note={injected ? `injected: ${injected.title}` : 'no anomaly injected'}>
      {!items.length && (
        <Empty>
          {injected
            ? 'Nothing tripped. At this severity the anomaly stays inside every check threshold — try raising it.'
            : 'Nominal engagement — no check tripped, nothing to diagnose.'}
        </Empty>
      )}
      {!!items.length && !shown.length && (
        <Empty>
          No diagnosis yet at T+{fmt.num(time, 1)} s.{' '}
          <button type="button" className="link" onClick={() => onSeek(items[0].t)}>
            jump to first diagnosis (T+{fmt.num(items[0].t, 1)} s)
          </button>
        </Empty>
      )}
      <div className="grid" style={{ gap: 10 }}>
        {shown.map((d, i) => {
          const correct = injected && d.cause === injected.key
          return (
            <div key={`${d.cause}-${i}`} className="diag-card" style={{ borderColor: CATEGORY_COLOR[d.category] }}>
              <div className="diag-head">
                <span className="bright">{d.title}</span>
                <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <Pill color={CATEGORY_COLOR[d.category]}>{d.category}</Pill>
                  <Pill color={INK.secondary}>{fmt.pct(d.confidence)}</Pill>
                  {injected && i === 0 && (
                    <Pill color={correct ? STATUS.good : STATUS.critical}>{correct ? 'matches injected' : 'mismatch'}</Pill>
                  )}
                </span>
              </div>
              <button type="button" className="link small mono" onClick={() => onSeek(d.t)}>
                attributed at T+{fmt.num(d.t, 1)} s
                {result.config.fault && ` (+${fmt.num(d.t - result.config.onset_s, 1)} s after onset)`}
              </button>
              <ul className="clean">
                {d.evidence.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
              <div className="small muted mono" style={{ marginTop: 6 }}>
                RECOMMENDED
              </div>
              <ul className="clean">
                {d.actions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>
      {pending > 0 && shown.length > 0 && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          {pending} more after the playhead.
        </p>
      )}
    </Panel>
  )
}

function AlertLog({ result, time, labels, onSeek }) {
  const events = result?.events ?? []
  return (
    <Panel title="Alert log" note={`${events.filter((e) => e.t <= time).length} / ${events.length} raised so far`}>
      {!events.length ? (
        <Empty>No alerts over the whole engagement.</Empty>
      ) : (
        <div className="scroll" style={{ maxHeight: 360 }}>
          <table className="data">
            <thead>
              <tr>
                <th>T+</th>
                <th>Level</th>
                <th>Check</th>
                <th style={{ textAlign: 'right' }}>Value</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => {
                const future = e.t > time
                return (
                  <tr
                    key={`${e.check}-${e.t}`}
                    className={`clickable ${Math.abs(e.t - time) < 0.11 ? 'selected' : ''}`}
                    style={{ opacity: future ? 0.4 : 1 }}
                    onClick={() => onSeek(e.t)}
                  >
                    <td className="num">{fmt.num(e.t, 1)}</td>
                    <td>
                      <Pill color={SEVERITY_COLOR[e.level]}>{e.level}</Pill>
                    </td>
                    <td>{labels[e.check] ?? e.check}</td>
                    <td className="num">{e.value}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
