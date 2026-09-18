// Vehicle Studio — design a rocket and satellite, analyse them, fly them.
//
// Three ways to start: a preset, a plain-English request (LLM with a
// deterministic fallback), or editing numbers directly. Every change is
// re-analysed on the backend (debounced) with the same model the launch panel
// flies, so the preview, the verdict and the flight never disagree.

import { useEffect, useMemo, useRef, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import api from '../services/api'
import { InlineNotice } from '../components/Disclaimer'
import { Empty, Panel, Pill, fmt } from '../components/primitives'
import { INK, SERIES, STATUS, axisProps, tooltipStyle } from '../theme'

const STAGE_TEMPLATE = { name: 'New stage', thrust_kn: 100, isp_s: 340, propellant_t: 10, dry_t: 1, engines: 1 }

export default function VehicleStudio({ design, onDesign, navigate }) {
  const [presets, setPresets] = useState([])
  const [report, setReport] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const timer = useRef(null)

  useEffect(() => {
    api
      .vehiclePresets()
      .then((data) => {
        setPresets(data.presets)
        if (!design && data.presets[0]) onDesign(data.presets[0].design)
      })
      .catch((err) => setError(err.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Debounced re-analysis on every edit.
  useEffect(() => {
    if (!design) return undefined
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      api
        .analyseVehicle(design)
        .then((data) => {
          setReport(data)
          setError(null)
        })
        .catch((err) => setError(err.message.replace(/^\d+\s/, '')))
    }, 350)
    return () => window.clearTimeout(timer.current)
  }, [design])

  const generate = async () => {
    if (!prompt.trim()) return
    setBusy(true)
    setError(null)
    try {
      const data = await api.generateVehicle(prompt.trim())
      onDesign(data.design)
      setReport(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const update = (path, value) => {
    const next = structuredClone(design)
    let target = next
    for (const key of path.slice(0, -1)) target = target[key]
    target[path.at(-1)] = value
    onDesign(next)
  }

  const analysis = report?.analysis

  return (
    <div className="studio">
      <div className="studio-prompt">
        <form
          className="composer compact"
          onSubmit={(event) => {
            event.preventDefault()
            generate()
          }}
        >
          <input
            type="text"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Describe a vehicle — “3-stage rocket for a 400 kg radar satellite to 700 km”"
            aria-label="Describe a vehicle to generate"
          />
          <button type="submit" className="btn primary" disabled={busy || !prompt.trim()}>
            {busy ? 'Generating…' : 'Generate'}
          </button>
        </form>
        <div className="choice-row">
          {presets.map((p) => (
            <button key={p.key} type="button" className="choice" onClick={() => onDesign(p.design)} title={p.notes}>
              {p.name}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="banner" role="alert">
          <div className="banner-text">{error}</div>
        </div>
      )}

      {!design ? (
        <Empty>Loading presets…</Empty>
      ) : (
        <div className="studio-grid">
          <div className="grid" style={{ gap: 'var(--gap)', alignContent: 'start' }}>
            <Panel title="Vehicle" note={report?.source ? `source: ${report.source}` : undefined}>
              <VehicleDrawing design={design} />
              <div className="control-row" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="btn primary"
                  disabled={!analysis}
                  onClick={() => navigate('assurance')}
                  title="The Test setup panel will offer this design as the vehicle"
                >
                  Fly in Flight Assurance
                </button>
                <button type="button" className="btn" onClick={() => downloadJson(design)}>
                  Export JSON
                </button>
              </div>
            </Panel>
            <Verdict analysis={analysis} preview={report?.preview} />
          </div>

          <div className="grid" style={{ gap: 'var(--gap)', alignContent: 'start' }}>
            <Panel title="Launcher">
              <div className="form-grid">
                <TextField label="Name" value={design.rocket.name} onChange={(v) => update(['rocket', 'name'], v)} />
                <NumberField label="Fairing (t)" value={design.rocket.fairing_t} step={0.05} onChange={(v) => update(['rocket', 'fairing_t'], v)} />
                <NumberField label="Target orbit (km)" value={design.target_altitude_km} step={10} min={160} max={2000} onChange={(v) => update(['target_altitude_km'], v)} />
                <ColorField label="Colour" value={design.rocket.color} onChange={(v) => update(['rocket', 'color'], v)} />
              </div>
              <div className="stage-table" role="table" aria-label="Stages">
                <div className="stage-row head" role="row">
                  <span>Stage</span>
                  <span>Thrust kN</span>
                  <span>Isp s</span>
                  <span>Propellant t</span>
                  <span>Dry t</span>
                  <span />
                </div>
                {design.rocket.stages.map((stage, i) => (
                  <div className="stage-row" role="row" key={i}>
                    <input type="text" value={stage.name} aria-label={`Stage ${i + 1} name`} onChange={(e) => update(['rocket', 'stages', i, 'name'], e.target.value)} />
                    <NumberInput value={stage.thrust_kn} step={10} label="thrust" onChange={(v) => update(['rocket', 'stages', i, 'thrust_kn'], v)} />
                    <NumberInput value={stage.isp_s} step={5} label="isp" onChange={(v) => update(['rocket', 'stages', i, 'isp_s'], v)} />
                    <NumberInput value={stage.propellant_t} step={1} label="propellant" onChange={(v) => update(['rocket', 'stages', i, 'propellant_t'], v)} />
                    <NumberInput value={stage.dry_t} step={0.1} label="dry mass" onChange={(v) => update(['rocket', 'stages', i, 'dry_t'], v)} />
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Remove stage ${i + 1}`}
                      disabled={design.rocket.stages.length <= 1}
                      onClick={() => update(['rocket', 'stages'], design.rocket.stages.filter((_, j) => j !== i))}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="btn"
                style={{ marginTop: 10 }}
                disabled={design.rocket.stages.length >= 4}
                onClick={() => update(['rocket', 'stages'], [...design.rocket.stages, { ...STAGE_TEMPLATE }])}
              >
                Add stage
              </button>
              {analysis && <StageTable stages={analysis.stages} />}
            </Panel>

            <Panel title="Satellite">
              <div className="form-grid">
                <TextField label="Name" value={design.satellite.name} onChange={(v) => update(['satellite', 'name'], v)} />
                <TextField label="Payload" value={design.satellite.payload} onChange={(v) => update(['satellite', 'payload'], v)} />
                <NumberField label="Mass (kg)" value={design.satellite.mass_kg} step={5} onChange={(v) => update(['satellite', 'mass_kg'], v)} />
                <NumberField label="Solar array (W)" value={design.satellite.solar_w} step={5} onChange={(v) => update(['satellite', 'solar_w'], v)} />
                <NumberField label="Battery (Wh)" value={design.satellite.battery_wh} step={10} onChange={(v) => update(['satellite', 'battery_wh'], v)} />
                <NumberField label="Bus load (W)" value={design.satellite.base_load_w} step={5} onChange={(v) => update(['satellite', 'base_load_w'], v)} />
                <NumberField label="Payload load (W)" value={design.satellite.payload_load_w} step={5} onChange={(v) => update(['satellite', 'payload_load_w'], v)} />
                <ColorField label="Colour" value={design.satellite.color} onChange={(v) => update(['satellite', 'color'], v)} />
              </div>
              {analysis && (
                <div className="tile-grid" style={{ marginTop: 12 }}>
                  <Mini label="Orbit period" value={`${fmt.num(analysis.satellite.orbit_period_min, 1)} min`} />
                  <Mini label="Eclipse" value={`${fmt.num(analysis.satellite.eclipse_min, 1)} min`} />
                  <Mini
                    label="Energy margin"
                    value={`${fmt.num(analysis.satellite.energy_margin_pct, 1)} %`}
                    color={analysis.satellite.energy_margin_pct < -3 ? STATUS.warning : undefined}
                  />
                  <Mini
                    label="Eclipse DoD"
                    value={`${fmt.num(analysis.satellite.eclipse_dod_pct, 0)} %`}
                    color={analysis.satellite.eclipse_dod_pct > 40 ? STATUS.warning : undefined}
                  />
                </div>
              )}
              <p className="small muted" style={{ margin: '10px 0 0', lineHeight: 1.5 }}>
                On orbit, telemetry is normalised to the reference bus: a satellite sized on the reference ratios
                flies nominal telemetry, and an undersized array or battery shows up as a power anomaly for Astrix
                to catch.
              </p>
            </Panel>
          </div>
        </div>
      )}
    </div>
  )
}

function Verdict({ analysis, preview }) {
  if (!analysis) return <Panel title="Performance"><Empty>Analysing…</Empty></Panel>
  const good = analysis.feasible
  return (
    <Panel title="Performance" accent={good ? STATUS.good : STATUS.critical} alert={!good}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <Pill color={good ? STATUS.good : STATUS.critical}>{analysis.verdict}</Pill>
        {preview?.aborted && <Pill color={STATUS.critical}>ascent aborts: {fmt.title(preview.abort_reason)}</Pill>}
      </div>
      <div className="tile-grid">
        <Mini label="Total Δv" value={`${fmt.num(analysis.total_delta_v_ms / 1000, 2)} km/s`} />
        <Mini label="Required" value={`${fmt.num(analysis.required_delta_v_ms / 1000, 2)} km/s`} />
        <Mini label="Margin" value={`${fmt.num(analysis.margin_ms, 0)} m/s`} color={analysis.margin_ms < 0 ? STATUS.critical : undefined} />
        <Mini label="Gross mass" value={`${fmt.num(analysis.gross_mass_t, 2)} t`} />
        <Mini label="Liftoff T/W" value={fmt.num(analysis.stages[0]?.twr, 2)} color={analysis.stages[0]?.twr < 1.15 ? STATUS.warning : undefined} />
        <Mini label="Payload fraction" value={`${fmt.num(analysis.payload_fraction_pct, 2)} %`} />
      </div>
      {preview?.track?.length > 0 && <AscentChart track={preview.track} />}
      {analysis.warnings.length > 0 && (
        <ul className="warn-list">
          {analysis.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <InlineNotice>{analysis.disclaimer}</InlineNotice>
    </Panel>
  )
}

function AscentChart({ track }) {
  const data = useMemo(() => track.filter((p) => p.t >= 0), [track])
  return (
    <div style={{ height: 180, marginTop: 14 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={INK.grid} vertical={false} />
          <XAxis dataKey="t" {...axisProps} tickFormatter={(v) => `${Math.round(v)}s`} />
          <YAxis yAxisId="alt" {...axisProps} width={44} />
          <YAxis yAxisId="speed" orientation="right" {...axisProps} width={36} />
          <Tooltip {...tooltipStyle} labelFormatter={(v) => `T+${Math.round(v)} s`} />
          <Line yAxisId="alt" type="monotone" dataKey="alt" name="altitude km" stroke={SERIES[0]} dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line yAxisId="speed" type="monotone" dataKey="speed" name="speed km/s" stroke={SERIES[1]} dot={false} strokeWidth={2} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function StageTable({ stages }) {
  return (
    <table className="data" style={{ marginTop: 12 }}>
      <thead>
        <tr>
          <th>Stage</th>
          <th>Δv m/s</th>
          <th>Burn s</th>
          <th>T/W</th>
          <th>Burnout g</th>
        </tr>
      </thead>
      <tbody>
        {stages.map((s) => (
          <tr key={s.index}>
            <td>{s.name}</td>
            <td className="mono">{fmt.num(s.delta_v_ms, 0)}</td>
            <td className="mono">{fmt.num(s.burn_time_s, 0)}</td>
            <td className="mono">{fmt.num(s.twr, 2)}</td>
            <td className="mono" style={s.burnout_accel_g > 6 ? { color: STATUS.warning } : undefined}>
              {fmt.num(s.burnout_accel_g, 1)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Side elevation: stage heights scale with propellant, widths with thrust.
function VehicleDrawing({ design }) {
  const stages = design.rocket.stages
  const maxProp = Math.max(...stages.map((s) => s.propellant_t))
  const maxThrust = Math.max(...stages.map((s) => s.thrust_kn))
  const heights = stages.map((s) => 40 + 110 * Math.sqrt(s.propellant_t / maxProp))
  const widths = stages.map((s) => 26 + 18 * Math.sqrt(s.thrust_kn / maxThrust))
  const fairingW = Math.max(...widths.slice(-1), 28)
  const cx = 110
  let y = 20 + 60
  const blocks = []
  for (let i = stages.length - 1; i >= 0; i -= 1) {
    blocks.push({ i, y, h: heights[i], w: widths[i] })
    y += heights[i]
  }
  const sat = design.satellite
  const span = 30 + Math.min(70, Math.sqrt(sat.solar_w) * 3)

  return (
    <div className="vehicle-drawing">
      <svg viewBox={`0 0 220 ${y + 30}`} role="img" aria-label={`${design.rocket.name} side view`}>
        <g>
          <path
            d={`M${cx - fairingW / 2} 80 L${cx - fairingW / 2} 50 Q${cx} ${10} ${cx + fairingW / 2} 50 L${cx + fairingW / 2} 80 Z`}
            fill={design.rocket.color}
            opacity="0.92"
          />
          {blocks.map((b) => (
            <g key={b.i}>
              <rect x={cx - b.w / 2} y={b.y} width={b.w} height={b.h - 3} rx="3" fill={design.rocket.color} opacity={b.i % 2 ? 0.8 : 1} />
              <text x={cx} y={b.y + b.h / 2 + 4} textAnchor="middle" fontSize="11" fill="#1a1a1a" fontFamily="var(--mono)">
                S{b.i + 1}
              </text>
            </g>
          ))}
          <path d={`M${cx - widths[0] / 2} ${y - 3} l-8 18 h${widths[0] + 16} l-8 -18 Z`} fill="#6b6b73" />
        </g>
      </svg>
      <svg viewBox="0 0 220 120" role="img" aria-label={`${sat.name} satellite`}>
        <rect x={110 - span - 16} y="50" width={span} height="20" fill={sat.color} stroke="#8fb7ee" strokeWidth="1" />
        <rect x={126} y="50" width={span} height="20" fill={sat.color} stroke="#8fb7ee" strokeWidth="1" />
        <rect x="94" y="40" width="32" height="40" rx="3" fill="#d9d4c3" />
        <circle cx="110" cy="60" r="5" fill={INK.muted} />
        <text x="110" y="104" textAnchor="middle" fontSize="11" fill={INK.secondary}>
          {sat.name} · {fmt.num(sat.mass_kg, 0)} kg · {sat.bus}
        </text>
      </svg>
    </div>
  )
}

function Mini({ label, value, color }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value" style={{ fontSize: 17, ...(color ? { color } : {}) }}>
        {value}
      </div>
    </div>
  )
}

function NumberInput({ value, step, label, onChange, min, max }) {
  return (
    <input
      type="number"
      value={value}
      step={step}
      min={min ?? 0}
      max={max}
      aria-label={label}
      onChange={(event) => {
        const n = Number(event.target.value)
        if (Number.isFinite(n) && event.target.value !== '') onChange(n)
      }}
    />
  )
}

function NumberField({ label, ...props }) {
  return (
    <label className="stack-field">
      <span>{label}</span>
      <NumberInput label={label} {...props} />
    </label>
  )
}

function TextField({ label, value, onChange }) {
  return (
    <label className="stack-field">
      <span>{label}</span>
      <input type="text" value={value} maxLength={40} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function ColorField({ label, value, onChange }) {
  return (
    <label className="stack-field">
      <span>{label}</span>
      <input type="color" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function downloadJson(design) {
  const blob = new Blob([JSON.stringify(design, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${design.rocket.name.replace(/\W+/g, '-').toLowerCase()}.json`
  a.click()
  URL.revokeObjectURL(url)
}
