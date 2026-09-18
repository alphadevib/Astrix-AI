// Hardware Link — Arduino-class prototypes in the loop.
//
// Connect a board over Web Serial (or the built-in emulator), watch its live
// sensors, send commands, and inject faults on the chip itself. While a
// Flight Assurance mission is on orbit, calibrated readings perturb the
// simulated spacecraft, so Astrix detects and recovers from real hardware
// behaviour — and its approved recovery actions are written back to the board.

import { useEffect, useMemo, useState } from 'react'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from 'recharts'
import api from '../services/api'
import { InlineNotice } from '../components/Disclaimer'
import Icon from '../components/icons'
import { Empty, Panel, Pill, fmt } from '../components/primitives'
import { INK, SERIES, STATUS, axisProps, tooltipStyle } from '../theme'

const FAULTS = [
  ['TEMP_BIAS', 'Thermistor bias'],
  ['SENSOR_STUCK', 'Stuck sensor'],
  ['VIB_SPIKE', 'Vibration spike'],
  ['GYRO_DRIFT', 'Gyro drift'],
  ['BROWNOUT', 'Brownout'],
  ['OVERCURRENT', 'Overcurrent'],
  ['MOTOR_STALL', 'Motor stall'],
  ['DROPOUT', 'Packet dropout'],
]

const ACTIONS = [
  ['ACT ISOLATE_WHEEL', 'Isolate wheel'],
  ['ACT RESTART_WHEEL', 'Restart wheel'],
  ['ACT POWER_SAVE', 'Power save'],
  ['ACT SAFE_MODE', 'Safe mode'],
  ['ACT NOMINAL_MODE', 'Nominal mode'],
  ['ACT REDUNDANT_SENSOR', 'Backup sensor'],
  ['ACT RECAL_GYRO', 'Recalibrate gyro'],
]

const CHANNELS = [
  { key: 'temp_c', label: 'Temperature', unit: '°C', digits: 2 },
  { key: 'bus_v', label: 'Bus voltage', unit: 'V', digits: 3 },
  { key: 'current_a', label: 'Current', unit: 'A', digits: 3 },
  { key: 'vib_g', label: 'Vibration', unit: 'g', digits: 3 },
  { key: 'rpm', label: 'Wheel', unit: 'rpm', digits: 0 },
  { key: 'light', label: 'Illumination', unit: '', digits: 2 },
]

export default function HardwareLink({ link, stream }) {
  const [fault, setFault] = useState('VIB_SPIKE')
  const [severity, setSeverity] = useState(0.8)
  const [custom, setCustom] = useState('')
  const [server, setServer] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    const tick = () =>
      api
        .hardwareStatus()
        .then((data) => alive && setServer(data))
        .catch(() => {})
    tick()
    const id = window.setInterval(tick, 1500)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  const run = async (fn) => {
    setError(null)
    try {
      await fn()
    } catch (err) {
      setError(err.message)
    }
  }

  const latest = link.latest ?? (server?.connected ? server.latest : null)
  const inOrbit = stream.mission?.running && stream.mission?.phase === 'ORBIT'
  const blending = Boolean(server?.connected && server?.calibrated && server?.overlay_enabled && inOrbit)

  return (
    <div className="grid" style={{ gap: 'var(--gap)' }}>
      {error && (
        <div className="banner" role="alert">
          <div className="banner-text">{error}</div>
          <button type="button" className="banner-close" aria-label="Dismiss" onClick={() => setError(null)}>
            ×
          </button>
        </div>
      )}

      <Panel
        title="Connection"
        note={link.connected ? `${link.kind === 'emulator' ? 'software board' : 'USB serial'} · ${link.readings.length} readings buffered` : 'not connected'}
      >
        <div className="control-row">
          {link.connected ? (
            <button type="button" className="btn" onClick={() => run(link.disconnect)}>
              Disconnect
            </button>
          ) : (
            <>
              <button
                type="button"
                className="btn primary"
                disabled={!link.supported}
                onClick={() => run(() => link.connect('usb'))}
                title={link.supported ? 'Pick the board’s serial port' : 'Web Serial needs Chrome or Edge over HTTPS or localhost'}
              >
                <Icon name="usb" size={15} /> Connect board
              </button>
              <button type="button" className="btn" onClick={() => run(() => link.connect('emulator'))}>
                Use emulator
              </button>
            </>
          )}
          <Pill color={server?.connected ? STATUS.good : INK.muted}>
            backend {server?.connected ? `receiving · ${server.transport}` : 'no device'}
          </Pill>
          {server?.connected && (
            <Pill color={server.calibrated ? STATUS.good : STATUS.warning}>
              {server.calibrated ? 'baseline calibrated' : `calibrating ${server.calibration_progress}/${server.calibration_samples}`}
            </Pill>
          )}
          <Pill color={blending ? STATUS.good : INK.muted}>
            {blending ? 'in the loop with the orbiting spacecraft' : 'not blended into a mission'}
          </Pill>
        </div>
        {!link.supported && (
          <p className="small muted" style={{ margin: '10px 0 0' }}>
            This browser has no Web Serial support. Use Chrome or Edge, run the serial bridge
            (<code>python -m backend.app.hardware.bridge --port COM3</code>), or try the emulator.
          </p>
        )}
        {link.error && <p className="small" style={{ color: STATUS.critical, margin: '10px 0 0' }}>{link.error}</p>}
      </Panel>

      <div className="tile-grid">
        {CHANNELS.map((c) => (
          <div className="tile" key={c.key}>
            <div className="tile-label">{c.label}</div>
            <div className="tile-value">
              {latest?.[c.key] == null ? '—' : fmt.num(latest[c.key], c.digits)}
              {c.unit && <span className="tile-unit">{c.unit}</span>}
            </div>
          </div>
        ))}
        <div className="tile">
          <div className="tile-label">Board state</div>
          <div className="tile-value" style={{ fontSize: 15 }}>
            {latest ? fmt.title(latest.mode) : '—'}
          </div>
          <div className="tile-sub">fault {latest ? fmt.title(latest.fault).toLowerCase() : '—'}</div>
        </div>
      </div>

      <div className="grid cols-2">
        <Panel title="Fault injection" note="Corrupts readings on the chip, ramping over ~20 s">
          <div className="choice-row" role="radiogroup" aria-label="Fault">
            {FAULTS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="radio"
                aria-checked={fault === key}
                className={`choice ${fault === key ? 'active' : ''}`}
                onClick={() => setFault(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="stack-field" style={{ marginTop: 12 }}>
            <span>Severity {Math.round(severity * 100)}%</span>
            <input type="range" min="0.1" max="1" step="0.05" value={severity} onChange={(e) => setSeverity(Number(e.target.value))} />
          </label>
          <div className="control-row" style={{ marginTop: 12 }}>
            <button type="button" className="btn primary" onClick={() => run(() => link.send(`INJECT ${fault} ${severity.toFixed(2)}`))}>
              Inject on hardware
            </button>
            <button type="button" className="btn" onClick={() => run(() => link.send('CLEAR'))}>
              Clear
            </button>
          </div>
          {!link.connected && (
            <p className="small muted" style={{ margin: '10px 0 0' }}>
              With no board connected here, commands are queued on the backend for a serial bridge to deliver.
            </p>
          )}
        </Panel>

        <Panel title="Commands" note="Recovery actions drive real actuators">
          <div className="choice-row">
            {ACTIONS.map(([command, label]) => (
              <button key={command} type="button" className="choice" onClick={() => run(() => link.send(command))}>
                {label}
              </button>
            ))}
          </div>
          <form
            className="control-row"
            style={{ marginTop: 12 }}
            onSubmit={(event) => {
              event.preventDefault()
              if (custom.trim()) run(() => link.send(custom.trim()).then(() => setCustom('')))
            }}
          >
            <input
              type="text"
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              placeholder="RATE 10 · MOTOR 200 · STATUS"
              aria-label="Raw command"
              style={{ flex: 1, fontFamily: 'var(--mono)' }}
            />
            <button type="submit" className="btn">
              Send
            </button>
            <button type="button" className="btn" onClick={() => run(async () => setServer(await api.hardwareCalibrate()))}>
              Recalibrate
            </button>
            <label className="field">
              <input
                type="checkbox"
                checked={server?.overlay_enabled ?? true}
                onChange={(e) => run(async () => setServer(await api.hardwareOverlay(e.target.checked)))}
              />
              blend into mission
            </label>
          </form>
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Live sensors">
          <SensorChart readings={link.readings} />
        </Panel>
        <Panel title="Serial log" note="tx = to board · rx = from board">
          <CommandLog entries={link.log.length ? link.log : (server?.log ?? []).slice().reverse()} />
        </Panel>
      </div>

      <Panel title="Wiring" note="hardware/arduino/astrix_hil/astrix_hil.ino">
        <table className="data">
          <thead>
            <tr>
              <th>Pin</th>
              <th>Part</th>
              <th>Stands in for</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['A0', '10k NTC thermistor + 10k divider', 'Bus temperature sensor'],
              ['A1', '10k/10k voltage divider', 'Battery bus voltage'],
              ['A2', 'ACS712-05B current sensor', 'Load current'],
              ['A3', 'LDR + 10k divider', 'Solar array illumination'],
              ['SDA/SCL', 'MPU-6050 IMU (0x68)', 'Wheel vibration and gyro rates'],
              ['D2', 'Hall sensor / encoder, 1 pulse per rev', 'Reaction wheel speed'],
              ['D5', 'Logic-level MOSFET + DC motor', 'Reaction wheel'],
              ['D7', 'Relay or MOSFET + load', 'Payload power line'],
            ].map(([pin, part, role]) => (
              <tr key={pin}>
                <td className="mono">{pin}</td>
                <td>{part}</td>
                <td>{role}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <InlineNotice>
          Prototype results are still hypothetical until they are checked against qualified test data. Never connect this
          firmware to flight hardware.
        </InlineNotice>
      </Panel>
    </div>
  )
}

function SensorChart({ readings }) {
  const data = useMemo(
    () =>
      readings.slice(-160).map((r, i) => ({
        i,
        temp: r.temp_c,
        vib: r.vib_g == null ? null : r.vib_g * 10,
        volts: r.bus_v,
      })),
    [readings],
  )
  if (!data.length) return <Empty>Connect a board or the emulator to see live readings.</Empty>
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke={INK.grid} vertical={false} />
          <XAxis dataKey="i" hide />
          <YAxis {...axisProps} width={44} />
          <Tooltip {...tooltipStyle} />
          <Line type="monotone" dataKey="temp" name="temp °C" stroke={SERIES[0]} dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="vib" name="vibration ×10 g" stroke={SERIES[1]} dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="volts" name="bus V" stroke={SERIES[2]} dot={false} strokeWidth={2} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function CommandLog({ entries }) {
  if (!entries.length) return <Empty>No traffic yet.</Empty>
  return (
    <ol className="serial-log">
      {entries.slice(0, 40).map((e, i) => (
        <li key={i} className={e.direction}>
          <span className="mono dir">{e.direction}</span>
          <span className="mono text">{e.text}</span>
          {e.source && <span className="src">{e.source}</span>}
        </li>
      ))}
    </ol>
  )
}
