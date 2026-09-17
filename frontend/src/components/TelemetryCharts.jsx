// Live telemetry charts.
//
// Four separate charts rather than one busy one, because each measures a
// different quantity in different units. A single chart with two y-axes is the
// most common charting mistake there is: it lets the author place the crossing
// point wherever they like, which is to say it can imply any correlation at all.
// Same-unit series share a chart; different units never do.

import { useMemo } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { INK, SERIES, STATUS, axisProps, tooltipStyle } from '../theme'
import { Panel } from './primitives'

const HEIGHT = 168

// Compact y-axis ticks (two or three significant figures), so small channels such as
// pointing error (0.0455°) don't overflow the axis gutter.
function tick(value) {
  const abs = Math.abs(value)
  if (abs >= 100 || Number.isInteger(value)) return String(Math.round(value))
  return String(Number(value.toPrecision(abs < 1 ? 2 : 3)))
}

function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour12: false, minute: '2-digit', second: '2-digit' })
}

function Chart({ title, unit, data, series, domain, reference }) {
  return (
    <Panel title={title} note={unit}>
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="t" {...axisProps} minTickGap={48} />
          <YAxis {...axisProps} domain={domain ?? ['auto', 'auto']} width={46} tickFormatter={tick} />
          <Tooltip {...tooltipStyle} />
          {series.length > 1 && (
            <Legend
              verticalAlign="top"
              height={24}
              iconType="plainline"
              iconSize={14}
              wrapperStyle={{ fontSize: 11, color: INK.secondary }}
            />
          )}
          {reference && (
            <ReferenceLine
              y={reference.value}
              stroke={reference.color ?? STATUS.warning}
              strokeDasharray="4 4"
              strokeWidth={1}
              label={{
                value: reference.label,
                position: 'insideTopRight',
                fill: reference.color ?? STATUS.warning,
                fontSize: 10,
              }}
            />
          )}
          {series.map((entry, index) => (
            <Line
              key={entry.key}
              type="monotone"
              dataKey={entry.key}
              name={entry.label}
              stroke={entry.color ?? SERIES[index]}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Panel>
  )
}

export default function TelemetryCharts({ frames }) {
  // Hooks run before any early return: React requires the same hook order on
  // every render, and frames go from empty to populated when a mission starts.
  const data = useMemo(() => {
    if (!frames?.length) return []
    // If frames buffer is large, sample evenly for silky rendering performance
    const step = frames.length > 120 ? Math.ceil(frames.length / 120) : 1
    const sampled = []
    for (let i = 0; i < frames.length; i += step) {
      const frame = frames[i]
      sampled.push({
        t: clock(frame.timestamp),
        soc: Number(frame.state_of_charge?.toFixed(1) ?? 0),
        v_bat: Number(frame.battery_voltage?.toFixed(2) ?? 0),
        solar: Number(frame.solar_power?.toFixed(1) ?? 0),
        balance: Number(frame.power_balance?.toFixed(1) ?? 0),
        temp: Number(frame.temperature?.toFixed(1) ?? 0),
        temp2: Number(frame.temperature_secondary?.toFixed(1) ?? 0),
        pointing: Number(frame.attitude_error_deg?.toFixed(3) ?? 0),
        w1: Number(frame.wheel_1_vibration?.toFixed(2) ?? 0),
        w2: Number(frame.wheel_2_vibration?.toFixed(2) ?? 0),
        w3: Number(frame.wheel_3_vibration?.toFixed(2) ?? 0),
        w4: Number(frame.wheel_4_vibration?.toFixed(2) ?? 0),
        signal: Number(frame.communication_signal?.toFixed(1) ?? 0),
        loss: Number(frame.packet_loss?.toFixed(1) ?? 0),
        cpu: Number(frame.cpu_load?.toFixed(1) ?? 0),
        mem: Number(frame.memory_usage?.toFixed(1) ?? 0),
      })
    }
    return sampled
  }, [frames])

  if (!data.length) {
    return (
      <Panel title="Telemetry">
        <p className="empty">
          No telemetry yet. Start the mission to begin streaming from the simulated spacecraft.
        </p>
      </Panel>
    )
  }

  return (
    <div className="chart-grid">
      <Chart
        title="Reaction wheel vibration"
        unit="mm/s RMS · per wheel"
        data={data}
        series={[
          { key: 'w1', label: 'Wheel 1' },
          { key: 'w2', label: 'Wheel 2' },
          { key: 'w3', label: 'Wheel 3' },
          { key: 'w4', label: 'Wheel 4' },
        ]}
        reference={{ value: 1.2, label: 'degradation threshold' }}
      />
      <Chart
        title="Pointing error"
        unit="degrees"
        data={data}
        series={[{ key: 'pointing', label: 'Attitude error' }]}
        reference={{ value: 0.5, label: 'science pointing budget' }}
      />
      <Chart
        title="Power"
        unit="watts"
        data={data}
        series={[
          { key: 'solar', label: 'Solar input' },
          { key: 'balance', label: 'Net balance' },
        ]}
        reference={{ value: 0, label: 'break-even', color: INK.muted }}
      />
      <Chart
        title="Thermal"
        unit="degrees Celsius · primary vs redundant sensor"
        data={data}
        series={[
          { key: 'temp', label: 'Primary sensor' },
          { key: 'temp2', label: 'Redundant sensor' },
        ]}
        reference={{ value: 46, label: 'warning limit' }}
      />
      <Chart
        title="Battery state of charge"
        unit="percent"
        data={data}
        domain={[0, 100]}
        series={[{ key: 'soc', label: 'State of charge' }]}
        reference={{ value: 30, label: 'planning floor' }}
      />
      <Chart
        title="Communications & Downlink"
        unit="percent link quality & packet loss"
        data={data}
        domain={[0, 100]}
        series={[
          { key: 'signal', label: 'Signal Quality %' },
          { key: 'loss', label: 'Packet Loss %', color: STATUS.critical },
        ]}
        reference={{ value: 65, label: 'link margin min', color: STATUS.warning }}
      />
      <Chart
        title="Command & Data Handling"
        unit="percent compute & memory load"
        data={data}
        domain={[0, 100]}
        series={[
          { key: 'cpu', label: 'CPU Load %' },
          { key: 'mem', label: 'Memory %', color: SERIES[2] },
        ]}
        reference={{ value: 80, label: 'throttle threshold', color: STATUS.warning }}
      />
      <Panel title="Agentic Telemetry Correlation" note="Multi-Subsystem Analysis">
        <p className="small muted" style={{ lineHeight: 1.65, margin: 0 }}>
          ASTRIX-AI monitors cross-channel signatures in real time. For instance, in thermal excursions,
          the Diagnostic Agent checks if CPU load spiked; in thermal sensor faults, it cross-checks the
          redundant sensor to detect instrumentation failures before executing costly power shutdowns.
        </p>
      </Panel>
    </div>
  )
}
