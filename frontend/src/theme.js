// Chart and status colors.
//
// The dashboard is dark-only: a mission-control screen that flips to a white
// background in a dim room is worse than one that does not flip.
//
// The categorical order below is fixed and never cycled. It is the validated
// dark-mode order (blue, orange, aqua, yellow) — worst adjacent CVD ΔE 8.4,
// worst adjacent normal-vision ΔE 19.8 against a #1a1a19 surface (the current
// #131419 panel surface is darker, so contrast only improves). Charts with
// up to four series also carry direct end-labels as secondary encoding, so
// identity never rests on hue alone. Do not add a fifth line series to a chart:
// fold it into a second chart instead of inventing a hue.

export const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500']

// Reserved. Never used as a series color. `nominal` is an alias of `good`.
export const STATUS = {
  good: '#0ca30c',
  nominal: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
  info: '#3987e5',
}

// Neutrals match the CSS tokens in index.css.
export const SURFACE = '#131419'
export const INK = {
  primary: '#f3f4f6',
  secondary: '#b8bcc6',
  muted: '#7f8490',
  grid: '#262830',
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
    background: 'rgba(19, 20, 25, 0.96)',
    border: '1px solid #363943',
    borderRadius: 8,
    boxShadow: '0 10px 30px -10px rgba(0,0,0,0.7)',
    fontSize: 12,
    color: INK.primary,
  },
  labelStyle: { color: INK.secondary, fontSize: 11 },
  itemStyle: { color: INK.primary },
}
