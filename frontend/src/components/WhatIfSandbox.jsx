import { useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import api from '../services/api'
import { INK, SERIES, STATUS, axisProps, tooltipStyle } from '../theme'
import { Panel, Pill } from './primitives'

const ACTION_CATALOGUE = [
  { id: 'isolate_wheel_3', name: 'Isolate Wheel #3', subsystem: 'ADCS' },
  { id: 'switch_redundant_wheel_config', name: 'Switch to Redundant 3-Wheel Mode', subsystem: 'ADCS' },
  { id: 'reduce_wheel_speed', name: 'Reduce Wheel Speeds', subsystem: 'ADCS' },
  { id: 'restart_wheel_3', name: 'Power-Cycle Wheel #3', subsystem: 'ADCS' },
  { id: 'recalibrate_gyro_bias', name: 'Recalibrate Gyro Bias against Star Tracker', subsystem: 'ADCS' },
  { id: 'switch_to_redundant_sensor', name: 'Switch to Redundant Thermal Sensor', subsystem: 'THERMAL' },
  { id: 'enter_power_save', name: 'Enter Power Save (Shed Non-Essential Loads)', subsystem: 'POWER' },
  { id: 'reduce_payload_duty_cycle', name: 'Reduce Payload Duty Cycle', subsystem: 'POWER' },
  { id: 'switch_data_processing_mode', name: 'Switch Low-Compute Processing Mode', subsystem: 'CDH' },
  { id: 'alter_task_schedule', name: 'Alter Task Schedule', subsystem: 'CDH' },
  { id: 'reduce_downlink_rate', name: 'Reduce Downlink Bitrate', subsystem: 'COMMS' },
  { id: 'enter_safe_mode', name: 'Enter Coarse Safe Mode', subsystem: 'EPS' },
]

export default function WhatIfSandbox({ latestFrame, currentCycle }) {
  const [selectedAction, setSelectedAction] = useState('isolate_wheel_3')
  const [simulation, setSimulation] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const runSimulation = async () => {
    if (!latestFrame) return
    setLoading(true)
    setError(null)
    try {
      // Execute digital twin simulation preview
      const sim = await api.simulate(selectedAction, latestFrame)
      setSimulation(sim)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const trajectory = simulation?.trajectory
  const chartData = trajectory?.t?.map((t, idx) => ({
    t: `${t}s`,
    action_attitude: trajectory.attitude_error_deg?.[idx],
    baseline_attitude: trajectory.baseline_attitude_error_deg?.[idx],
    attitude_p95: trajectory.attitude_p95?.[idx],
    action_temp: trajectory.temperature?.[idx],
    baseline_temp: trajectory.baseline_temperature?.[idx],
    action_soc: trajectory.state_of_charge?.[idx],
    baseline_soc: trajectory.baseline_state_of_charge?.[idx],
  })) ?? []

  return (
    <Panel
      title="Interactive 'What-If' Simulation Sandbox"
      note="Digital Twin Monte Carlo Verification Preview"
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label className="field" style={{ margin: 0 }}>
          Hypothetical Action:
          <select
            value={selectedAction}
            onChange={(e) => setSelectedAction(e.target.value)}
            style={{ marginLeft: 6, minWidth: 260 }}
          >
            {ACTION_CATALOGUE.map((item) => (
              <option key={item.id} value={item.id}>
                [{item.subsystem}] {item.name}
              </option>
            ))}
          </select>
        </label>

        <button
          className="btn primary"
          onClick={runSimulation}
          disabled={loading || !latestFrame}
        >
          {loading ? 'Simulating...' : 'Run What-If Simulation'}
        </button>

        {simulation && (
          <Pill
            label={simulation.status === 'PASS' ? 'VERIFICATION: PASS' : 'VERIFICATION: FAIL'}
            color={simulation.status === 'PASS' ? STATUS.nominal : STATUS.critical}
          />
        )}
        {simulation?.probabilistic_confidence !== undefined && (
          <Pill
            label={`Monte Carlo: ${(simulation.probabilistic_confidence * 100).toFixed(1)}% Confidence`}
            color={STATUS.info}
          />
        )}
      </div>

      {error && (
        <p className="small" style={{ color: STATUS.critical, margin: '6px 0' }}>
          Simulation Error: {error}
        </p>
      )}

      {simulation && (
        <div>
          <p className="small" style={{ color: INK.secondary, margin: '4px 0 10px' }}>
            <strong>Twin Prediction:</strong> {simulation.summary}
          </p>

          <div className="grid cols-2" style={{ gap: 12 }}>
            <div style={{ background: 'rgba(0,0,0,0.2)', padding: 10, borderRadius: 6 }}>
              <div className="small muted" style={{ marginBottom: 4 }}>
                Pointing Error Projection (Action vs Do-Nothing Baseline)
              </div>
              <ResponsiveContainer width="100%" height={160}>
                <LineChart data={chartData} margin={{ top: 4, right: 10, bottom: 0, left: -20 }}>
                  <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="t" {...axisProps} minTickGap={30} />
                  <YAxis {...axisProps} width={42} />
                  <Tooltip {...tooltipStyle} />
                  <Legend verticalAlign="top" height={20} iconSize={10} wrapperStyle={{ fontSize: 10 }} />
                  <Line
                    type="monotone"
                    dataKey="action_attitude"
                    name="What-If Action"
                    stroke={SERIES[0]}
                    strokeWidth={2}
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="baseline_attitude"
                    name="Do-Nothing"
                    stroke={STATUS.critical}
                    strokeDasharray="4 4"
                    strokeWidth={1.5}
                    dot={false}
                  />
                  {chartData[0]?.attitude_p95 !== undefined && (
                    <Line
                      type="monotone"
                      dataKey="attitude_p95"
                      name="MC 95th %ile"
                      stroke={STATUS.warning}
                      strokeDasharray="2 2"
                      strokeWidth={1}
                      dot={false}
                    />
                  )}
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div style={{ background: 'rgba(0,0,0,0.2)', padding: 10, borderRadius: 6 }}>
              <div className="small muted" style={{ marginBottom: 6 }}>Verification Checks:</div>
              <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: INK.muted, textAlign: 'left' }}>
                    <th style={{ paddingBottom: 4 }}>Constraint</th>
                    <th style={{ paddingBottom: 4 }}>Result</th>
                    <th style={{ paddingBottom: 4 }}>Limit</th>
                    <th style={{ paddingBottom: 4 }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {simulation.checks?.map((check) => (
                    <tr key={check.name} style={{ borderTop: `1px solid ${INK.grid}` }}>
                      <td style={{ padding: '4px 0' }}>{check.name}</td>
                      <td style={{ padding: '4px 0' }}>{check.value}</td>
                      <td style={{ padding: '4px 0' }}>{check.limit}</td>
                      <td style={{ padding: '4px 0' }}>
                        <span style={{ color: check.passed ? STATUS.nominal : STATUS.critical }}>
                          {check.passed ? '✓ PASS' : '✗ FAIL'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </Panel>
  )
}
