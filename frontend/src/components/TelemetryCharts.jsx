// Live telemetry charts.
//
// Four separate charts rather than one busy one, because each measures a
// different quantity in different units. A single chart with two y-axes is the
// most common charting mistake there is: it lets the author place the crossing
// point wherever they like, which is to say it can imply any correlation at all.
// Same-unit series share a chart; different units never do.

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

function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour12: false, minute: '2-digit', second: '2-digit' })
}

function Chart({ title, unit, data, series, domain, reference }) {
  return (
    <Panel title={title} note={unit}>
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -18 }}>
          <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="t" {...axisProps} minTickGap={48} />
          <YAxis {...axisProps} domain={domain ?? ['auto', 'auto']} width={46} />
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
  if (!frames?.length) {
    return (
      <Panel title="Telemetry">
        <p className="empty">
          No telemetry yet. Start the mission to begin streaming from the simulated spacecraft.
        </p>
      </Panel>
    )
  }

  const data = frames.map((frame) => ({
    t: clock(frame.timestamp),
    soc: frame.state_of_charge,
    solar: frame.solar_power,
    balance: frame.power_balance,
    temp: frame.temperature,
    temp2: frame.temperature_secondary,
    pointing: frame.attitude_error_deg,
    w1: frame.wheel_1_vibration,
    w2: frame.wheel_2_vibration,
    w3: frame.wheel_3_vibration,
    w4: frame.wheel_4_vibration,
  }))

  return (
    <div className="grid cols-2">
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
      <Panel title="Why these are separate charts" note="method note">
        <p className="small muted" style={{ lineHeight: 1.65, margin: 0 }}>
          Each chart carries one unit. Vibration, pointing error, watts, degrees Celsius and percent
          never share an axis, so nothing here can imply a correlation by scaling choice. Series that
          do share a chart share a unit and are directly comparable — the four wheel traces above are
          the clearest example: the degrading wheel separates from its three siblings on the same
          scale, which is exactly the signature the Diagnostic Agent reasons about.
        </p>
      </Panel>
    </div>
  )
}
