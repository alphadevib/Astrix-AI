// Small shared building blocks. Kept in one file so the panel components stay
// focused on what they show rather than on how a pill or a tile is put together.

import { INK, LEVEL_COLOR, OUTCOME_COLOR, RISK_COLOR, SEVERITY_COLOR } from '../theme'

export function Panel({ title, note, children, style, className = '' }) {
  return (
    <section className={`panel ${className}`} style={style}>
      {(title || note) && (
        <header className="panel-head">
          {title && <h2 className="panel-title">{title}</h2>}
          {note && <span className="panel-note">{note}</span>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Pill({ color = INK.muted, children, title }) {
  return (
    <span className="pill" title={title} style={{ borderColor: color }}>
      <span className="dot" style={{ background: color }} aria-hidden="true" />
      {children}
    </span>
  )
}

export function SeverityPill({ severity, suppressed }) {
  if (!severity) return <Pill>no signal</Pill>
  const color = suppressed ? INK.muted : SEVERITY_COLOR[severity] ?? INK.muted
  return (
    <Pill color={color} title={suppressed ? 'Downgraded by context-aware suppression' : undefined}>
      {suppressed ? `${severity} · suppressed` : severity}
    </Pill>
  )
}

export function RiskPill({ risk }) {
  if (!risk) return null
  return <Pill color={RISK_COLOR[risk] ?? INK.muted}>{risk}</Pill>
}

export function LevelPill({ level }) {
  if (!level) return null
  return <Pill color={LEVEL_COLOR[level] ?? INK.muted}>{level}</Pill>
}

export function OutcomePill({ outcome }) {
  if (!outcome) return null
  return <Pill color={OUTCOME_COLOR[outcome] ?? INK.muted}>{outcome}</Pill>
}

export function VerdictPill({ status }) {
  if (!status) return null
  const pass = status === 'PASS'
  return <Pill color={pass ? '#0ca30c' : '#d03b3b'}>{pass ? 'PASS' : 'FAIL'}</Pill>
}

export function Tile({ label, value, unit, sub, color }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value" style={color ? { color } : undefined}>
        {value}
        {unit && <span className="tile-unit">{unit}</span>}
      </div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}

export function Empty({ children }) {
  return <p className="empty">{children}</p>
}

export function KeyValue({ items }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export const fmt = {
  num: (value, digits = 2) =>
    value === null || value === undefined || Number.isNaN(value) ? '—' : Number(value).toFixed(digits),
  pct: (value, digits = 0) =>
    value === null || value === undefined ? '—' : `${(Number(value) * 100).toFixed(digits)}%`,
  time: (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : '—'),
  title: (text) => (text ?? '').replace(/_/g, ' '),
}
