// Application frame: sidebar navigation, sticky status bar and the phone tab bar.
//
// The status bar is the one thing visible from every page and every scroll
// position, so it carries what an operator must never lose sight of: mission
// phase, ASTRIX's current severity, pending approvals and whether the feed is live.

import { SEVERITY_COLOR, STATUS, INK } from '../theme'
import { Pill, fmt } from './primitives'

const ICONS = {
  control: (
    <path d="M3 12h3l2.5-6 4 12 2.5-6H21" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  ),
  intercept: (
    <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    </g>
  ),
  memory: (
    <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </g>
  ),
}

function Icon({ name, size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      {ICONS[name]}
    </svg>
  )
}

function Logo() {
  return (
    <img
      src="/astrix-emblem.svg"
      alt="ASTRIX-AI"
      width="26"
      height="26"
      style={{ display: 'block', borderRadius: '4px', filter: 'drop-shadow(0 0 8px rgba(0, 240, 255, 0.5))' }}
    />
  )
}

function NavItems({ pages, current, badges }) {
  return pages.map((page) => (
    <a
      key={page.key}
      href={`#/${page.key}`}
      className="nav-item"
      aria-current={current === page.key ? 'page' : undefined}
      title={page.label}
    >
      <Icon name={page.key} />
      <span className="nav-text">{page.short ?? page.label}</span>
      {badges[page.key] > 0 && (
        <span className="nav-badge" aria-label={`${badges[page.key]} pending`}>
          {badges[page.key]}
        </span>
      )}
    </a>
  ))
}

export default function AppShell({ pages, current, stream, children }) {
  const { connected, reasoner, pipeline, mission, latest, detection, approval } = stream
  const page = pages.find((p) => p.key === current) ?? pages[0]
  const badges = { control: approval ? 1 : 0 }

  const running = Boolean(mission?.running)
  const failed = mission?.phase === 'FAILED'
  const phaseColor = failed ? STATUS.critical : running ? STATUS.good : INK.muted
  const phaseLabel = running ? fmt.title(mission.phase).toLowerCase() : failed ? 'runner failed' : 'stopped'
  const severity = detection?.suppressed ? 'NORMAL' : detection?.severity
  const inOrbit = running && mission?.phase === 'ORBIT'

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Logo />
          </span>
          <span className="brand-text">
            <h1>ASTRIX-AI</h1>
            <span className="tagline">Autonomous Spacecraft Intelligence</span>
          </span>
        </div>

        <nav className="nav" aria-label="Primary">
          <div className="nav-label">Operations</div>
          <NavItems pages={pages} current={current} badges={badges} />
        </nav>

        <div className="sidebar-foot">
          <div className="status-line" title={reasoner.reason ?? 'LLM reasoning active'}>
            <span>Reasoner</span>
            <strong style={{ color: reasoner.llm ? '#8fbdf3' : undefined }}>{reasoner.label}</strong>
          </div>
          <div className="status-line">
            <span>Autonomy</span>
            <strong>≤ {pipeline?.autonomy_limit ?? '—'}</strong>
          </div>
          {pipeline?.suppressed > 0 && (
            <div className="status-line">
              <span>Suppressed</span>
              <strong>{pipeline.suppressed}</strong>
            </div>
          )}
          <div className="status-line feed" title={connected ? 'Telemetry feed live' : 'Reconnecting to the backend'}>
            <span>Feed</span>
            <strong>
              <span className={`live-dot ${connected ? 'on' : 'off'}`} />
              <span className="feed-text">{connected ? 'live' : 'offline'}</span>
            </strong>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title">
            <h2>{page.label}</h2>
            <p>{page.description}</p>
          </div>
          <div className="topbar-status">
            {latest && (
              <span className="topbar-meta hide-sm">
                {latest.spacecraft_id} · MET {fmt.num(mission?.simulated_seconds ?? latest.seq, 0)} s
              </span>
            )}
            {approval && (
              <a href="#/control" style={{ textDecoration: 'none' }}>
                <Pill color={STATUS.warning}>approval</Pill>
              </a>
            )}
            {inOrbit && severity && (
              <span className="hide-sm">
                <Pill color={SEVERITY_COLOR[severity] ?? INK.muted}>{severity}</Pill>
              </span>
            )}
            <Pill color={phaseColor}>{phaseLabel}</Pill>
            <span
              className={`live-dot ${connected ? 'on' : 'off'}`}
              title={connected ? 'live' : 'reconnecting'}
              role="status"
              aria-label={connected ? 'Feed live' : 'Feed reconnecting'}
            />
          </div>
        </header>

        <main className="page">{children}</main>
      </div>

      <nav className="mobile-nav" aria-label="Primary">
        <NavItems pages={pages} current={current} badges={badges} />
      </nav>
    </div>
  )
}
