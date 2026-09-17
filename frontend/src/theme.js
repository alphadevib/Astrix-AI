// Chart and status colors.
//
// The dashboard is dark-only: a mission-control screen that flips to a white
// background in a dim room is worse than one that does not flip.
//
// The categorical order below is fixed and never cycled. It is the validated
// dark-mode order (blue, orange, aqua, yellow) — worst adjacent CVD ΔE 8.4,
// worst adjacent normal-vision ΔE 19.8 against the #1a1a19 surface. Charts with
// up to four series also carry direct end-labels as secondary encoding, so
// identity never rests on hue alone. Do not add a fifth line series to a chart:
// fold it into a second chart instead of inventing a hue.

export const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500']

// Reserved. Never used as a series color.
export const STATUS = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
}

export const SURFACE = '#1a1a19'
export const INK = {
  primary: '#ffffff',
  secondary: '#c3c2b7',
  muted: '#8a8a80',
  grid: '#2f2f2c',
}

export const SEVERITY_COLOR = {
  NORMAL: STATUS.good,
  WATCH: INK.secondary,
  WARNING: STATUS.warning,
  CRITICAL: STATUS.critical,
}

export const RISK_COLOR = {
  GREEN: STATUS.good,
  YELLOW: STATUS.warning,
  RED: STATUS.critical,
}

export const LEVEL_COLOR = {
  LOW: STATUS.good,
  MEDIUM: STATUS.warning,
  HIGH: STATUS.critical,
}

export const OUTCOME_COLOR = {
  SUCCESSFUL: STATUS.good,
  PARTIAL: STATUS.warning,
  FAILED: STATUS.critical,
  UNKNOWN: INK.muted,
}

// Shared Recharts axis/grid props — recessive by default, as the method requires.
export const axisProps = {
  stroke: INK.grid,
  tick: { fill: INK.muted, fontSize: 11 },
  tickLine: false,
}

export const tooltipStyle = {
  contentStyle: {
    background: '#232320',
    border: '1px solid #3a3a36',
    borderRadius: 6,
    fontSize: 12,
    color: INK.primary,
  },
  labelStyle: { color: INK.secondary, fontSize: 11 },
  itemStyle: { color: INK.primary },
}
