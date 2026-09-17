// Mission controls and the vitals strip.
//
// The fault menu separates real faults from the benign transient, because the
// benign case is the one that proves false-alarm suppression works, and a demo
// that hides it looks like it is only showing successes.
//
// Fault injection is only enabled in ORBIT: before deployment there is no
// satellite to inject into, and the backend refuses the request anyway.

import { useEffect, useState } from 'react'
import api from '../services/api'
import { INK, SERIES, STATUS } from '../theme'
import { Panel, Pill, Tile, fmt } from './primitives'

const LAUNCH_SPEEDS = [
  { value: 10, label: '10× (~60 s)' },
  { value: 20, label: '20× (~30 s)' },
  { value: 60, label: '60× (~10 s)' },
]

export function MissionControls({ mission, onError, vehicle }) {
  const [scenarios, setScenarios] = useState([])
  const [scenario, setScenario] = useState('wheel_degradation')
  const [interval, setIntervalValue] = useState(0.35)
  const [includeLaunch, setIncludeLaunch] = useState(true)
  const [launchScale, setLaunchScale] = useState(20)
  const [launchFault, setLaunchFault] = useState('none')
  const [useDesign, setUseDesign] = useState(Boolean(vehicle))
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .scenarios()
      .then((data) => setScenarios(data.scenarios ?? []))
      .catch((error) => onError?.(`could not load fault scenarios: ${error.message}`))
  }, [onError])

  const guard = async (action) => {
    setBusy(true)
    try {
      await action()
    } catch (error) {
      onError?.(error.message)
    } finally {
      setBusy(false)
    }
  }

  const faults = scenarios.filter((s) => s.is_fault)
  const benign = scenarios.filter((s) => !s.is_fault)
  const selected = scenarios.find((s) => s.key === scenario)
  const running = Boolean(mission?.running)
  const inOrbit = running && mission?.phase === 'ORBIT'
  const rate = Number(interval)
  const rateValid = Number.isFinite(rate) && rate >= 0.05 && rate <= 5

  let note = 'stopped'
  if (inOrbit) {
    note = `on orbit · MET ${fmt.num(mission.simulated_seconds, 0)} s`
  } else if (running) {
    note = `${fmt.title(mission.phase).toLowerCase()} · fault injection unlocks in orbit`
  } else if (mission?.phase === 'FAILED') {
    note = `runner failed: ${mission.error ?? 'see server log'}`
  }

  return (
    <Panel title="Test setup" note={note}>
      <div className="control-row">
        {running ? (
          <button className="btn" disabled={busy} onClick={() => guard(api.stopMission)}>
            Stop mission
          </button>
        ) : (
          <button
            className="btn primary"
            disabled={busy || !rateValid}
            onClick={() =>
              guard(() =>
                api.startMission({
                  interval: rate,
                  dt: 1.0,
                  include_launch: includeLaunch,
                  launch_time_scale: Number(launchScale),
                  launch_fault: launchFault === 'none' ? null : launchFault,
                  vehicle: useDesign && vehicle ? vehicle : null,
                }),
              )
            }
          >
            {includeLaunch ? 'Launch mission' : 'Start in orbit'}
          </button>
        )}

        <label className="field">
          vehicle
          <select
            value={useDesign && vehicle ? 'studio' : 'reference'}
            disabled={running}
            onChange={(event) => setUseDesign(event.target.value === 'studio')}
          >
            <option value="reference">Reference ASTRIX-LV</option>
            {vehicle && (
              <option value="studio">
                Studio: {vehicle.rocket.name} + {vehicle.satellite.name}
              </option>
            )}
          </select>
        </label>

        <label className="field">
          <input
            type="checkbox"
            checked={includeLaunch}
            disabled={running}
            onChange={(event) => setIncludeLaunch(event.target.checked)}
          />
          fly launch
        </label>

        {includeLaunch && (
          <label className="field">
            launch mode
            <select
              value={launchFault}
              disabled={running}
              onChange={(event) => setLaunchFault(event.target.value)}
            >
              <option value="none">Nominal Ascent</option>
              <option value="premature_meco">Abort: Premature MECO</option>
              <option value="ascent_thrust_loss">Fault: Thrust Deficit</option>
              <option value="max_q_excursion">Fault: Max-Q Spike</option>
            </select>
          </label>
        )}

        <label className="field">
          launch speed
          <select
            value={launchScale}
            disabled={running || !includeLaunch}
            onChange={(event) => setLaunchScale(Number(event.target.value))}
          >
            {LAUNCH_SPEEDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          orbit rate
          <input
            type="number"
            step="0.05"
            min="0.05"
            max="5"
            value={interval}
            disabled={running}
            aria-invalid={!rateValid}
            style={{ width: 72 }}
            onChange={(event) => setIntervalValue(event.target.value)}
          />
          s/frame
        </label>
      </div>

      <div className="control-row" style={{ marginTop: 10 }}>
        <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
          <optgroup label="Faults">
            {faults.map((s) => (
              <option key={s.key} value={s.key}>
                {s.title}
              </option>
            ))}
          </optgroup>
          <optgroup label="Not a fault (false-alarm test)">
            {benign.map((s) => (
              <option key={s.key} value={s.key}>
                {s.title}
              </option>
            ))}
          </optgroup>
        </select>

        <button
          className="btn primary"
          disabled={busy || !inOrbit || !selected}
          title={inOrbit ? undefined : 'Available once the satellite is deployed and ASTRIX is online'}
          onClick={() => guard(() => api.injectFault(scenario))}
        >
          Inject
        </button>
        <button
          className="btn"
          disabled={busy || !inOrbit || !mission?.active_scenario}
          onClick={() => guard(api.clearFault)}
        >
          Clear fault
        </button>
      </div>

      {selected && (
        <p className="small muted" style={{ margin: '10px 0 0', lineHeight: 1.55 }}>
          <strong style={{ color: selected.is_fault ? INK.secondary : STATUS.warning }}>
            {selected.title}
            {selected.is_fault ? '' : ' — NOT A FAULT'}:
          </strong>{' '}
          {selected.description} Ramps over {fmt.num(selected.ramp_seconds, 0)}s; expected signals:{' '}
          {selected.expected_signals?.join(', ')}.
        </p>
      )}

      {(mission?.active_scenario_title ||
        mission?.wheels_disabled?.length > 0 ||
        mission?.safe_mode ||
        mission?.power_save) && (
        <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {mission.active_scenario_title && (
            <Pill color={STATUS.warning}>active fault: {mission.active_scenario_title}</Pill>
          )}
          {mission.wheels_disabled?.length > 0 && (
            <Pill color={SERIES[1]}>wheel {mission.wheels_disabled.join(', ')} isolated</Pill>
          )}
          {mission.safe_mode && <Pill color={STATUS.critical}>safe mode</Pill>}
          {mission.power_save && <Pill color={SERIES[3]}>power save</Pill>}
        </div>
      )}
    </Panel>
  )
}

export function Vitals({ frame, resources }) {
  if (!frame) {
    return (
      <div className="tile-grid">
        {['Battery', 'Power', 'Temperature', 'Pointing'].map((label) => (
          <Tile key={label} label={label} value="—" />
        ))}
      </div>
    )
  }

  const socColor =
    frame.state_of_charge < 30 ? STATUS.critical : frame.state_of_charge < 50 ? STATUS.warning : INK.primary
  const tempColor =
    frame.temperature > 55 ? STATUS.critical : frame.temperature > 46 ? STATUS.warning : INK.primary
  const pointColor =
    frame.attitude_error_deg > 2 ? STATUS.critical : frame.attitude_error_deg > 0.5 ? STATUS.warning : INK.primary
  const worstVibration = Math.max(
    frame.wheel_1_vibration,
    frame.wheel_2_vibration,
    frame.wheel_3_vibration,
    frame.wheel_4_vibration,
  )
  const vibColor = worstVibration > 1.2 ? STATUS.warning : INK.primary

  return (
    <div className="tile-grid">
      <Tile
        label="Battery"
        value={fmt.num(frame.state_of_charge, 1)}
        unit="%"
        color={socColor}
        sub={`${fmt.num(frame.battery_voltage, 2)} V · ${fmt.num(frame.battery_current, 2)} A`}
      />
      <Tile
        label="Power balance"
        value={fmt.num(frame.power_balance, 0)}
        unit="W"
        color={frame.power_balance < 0 ? STATUS.warning : INK.primary}
        sub={`solar ${fmt.num(frame.solar_power, 0)} W${frame.in_eclipse ? ' · eclipse' : ''}`}
      />
      <Tile
        label="Temperature"
        value={fmt.num(frame.temperature, 1)}
        unit="°C"
        color={tempColor}
        sub={`redundant ${fmt.num(frame.temperature_secondary, 1)} °C`}
      />
      <Tile
        label="Pointing error"
        value={fmt.num(frame.attitude_error_deg, 3)}
        unit="°"
        color={pointColor}
        sub={`budget 0.50° · ${resources?.attitude_control_available === false ? '3-axis LOST' : '3-axis OK'}`}
      />
      <Tile
        label="Worst wheel"
        value={fmt.num(worstVibration, 2)}
        unit="mm/s"
        color={vibColor}
        sub={`${resources?.operational_wheels?.length ?? 4} of 4 operational`}
      />
      <Tile
        label="Link"
        value={fmt.num(frame.communication_signal, 0)}
        unit="%"
        sub={`packet loss ${fmt.num(frame.packet_loss, 2)}% · ${frame.ground_contact ? 'in contact' : 'no pass'}`}
      />
      <Tile
        label="Compute"
        value={fmt.num(frame.cpu_load, 0)}
        unit="%"
        sub={`memory ${fmt.num(frame.memory_usage, 0)}%`}
      />
      <Tile
        label="Mode"
        value={<span style={{ fontSize: 15 }}>{fmt.title(frame.mode)}</span>}
        sub={`fuel ${fmt.num(frame.fuel_level, 1)}% · frame ${frame.seq}`}
      />
    </div>
  )
}
