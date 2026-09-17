// Application frame: collapsible sidebar, quiet top bar, and the standing
// results notice. Modelled on agentic AI workspaces — the operator's attention
// belongs on the content; chrome stays out of the way.
//
// The top bar still carries what an operator must never lose sight of: mission
// phase, ASTRIX's current severity, pending approvals and whether the feed is live.

import { useEffect, useState } from 'react'
import { SEVERITY_COLOR, STATUS, INK } from '../theme'
import Icon from './icons'
import { Pill, fmt } from './primitives'
import ReasonerPicker from './ReasonerPicker'
import SettingsDialog from './SettingsDialog'
import { NoticeBar } from './Disclaimer'

const COLLAPSE_KEY = 'astrix.sidebarCollapsed'

function readCollapsed() {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

function NavItems({ pages, current, badges, onNavigate }) {
  return pages.map((page) => (
    <a
      key={page.key}
      href={`#/${page.key}`}
      className="nav-item"
      aria-current={current === page.key ? 'page' : undefined}
      title={page.label}
      onClick={onNavigate}
    >
      <Icon name={page.key} />
      <span className="nav-text">{page.label}</span>
      {badges[page.key] > 0 && (
        <span className="nav-badge" aria-label={`${badges[page.key]} pending`}>
          {badges[page.key]}
        </span>
      )}
    </a>
  ))
}

export default function AppShell({ pages, current, stream, hardware, onNewChat, children }) {
  const { connected, mission, latest, detection, approval } = stream
  const page = pages.find((p) => p.key === current) ?? pages[0]
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [drawer, setDrawer] = useState(false)
  const [settings, setSettings] = useState(false)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => setDrawer(false), [current])

  const toggleCollapsed = () => {
    setCollapsed((value) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, value ? '0' : '1')
      } catch {
        /* storage unavailable */
      }
      return !value
    })
  }

  const badges = { assurance: approval ? 1 : 0 }
  const running = Boolean(mission?.running)
  const failed = mission?.phase === 'FAILED'
  const phaseColor = failed ? STATUS.critical : running ? STATUS.good : INK.muted
  const phaseLabel = running ? fmt.title(mission.phase).toLowerCase() : failed ? 'runner failed' : 'idle'
  const severity = detection?.suppressed ? 'NORMAL' : detection?.severity
  const inOrbit = running && mission?.phase === 'ORBIT'
  const groups = [
    ['Workspace', pages.filter((p) => p.group === 'workspace')],
    ['Intelligence', pages.filter((p) => p.group === 'intelligence')],
  ]

  return (
    <div className={`shell ${collapsed ? 'collapsed' : ''} ${drawer ? 'drawer-open' : ''}`}>
      <aside className="sidebar" aria-label="Sidebar">
        <div className="sidebar-top">
          <a className="brand" href="/" title="Astrix-AI home">
            <img src="/astrix-emblem.svg" alt="" />
            <span className="brand-name">Astrix-AI</span>
          </a>
          <button
            type="button"
            className="icon-btn collapse-btn"
            onClick={toggleCollapsed}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <Icon name="sidebar" />
          </button>
        </div>

        <button type="button" className="new-chat" onClick={onNewChat} title="New conversation">
          <Icon name="plus" size={16} />
          <span>New conversation</span>
        </button>

        <nav className="nav" aria-label="Primary">
          {groups.map(([label, items]) => (
            <div key={label} style={{ display: 'grid', gap: 1 }}>
              <div className="nav-label">{label}</div>
              <NavItems pages={items} current={current} badges={badges} onNavigate={() => setDrawer(false)} />
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div
            className="foot-row"
            title={connected ? 'Telemetry feed live' : 'Backend unreachable — check Settings'}
            role="status"
          >
            <span className={`live-dot ${connected ? 'on' : 'off'}`} style={{ margin: '0 5px' }} />
            <span>Backend</span>
            <span className="foot-value">{connected ? 'live' : 'offline'}</span>
          </div>
          <div className="foot-row" title="Hardware-in-the-loop link">
            <Icon name="usb" size={16} />
            <span>Hardware</span>
            <span className="foot-value">{hardware?.connected ? hardware.kind : 'none'}</span>
          </div>
          <button type="button" className="foot-row" onClick={() => setSettings(true)}>
            <Icon name="settings" size={16} />
            <span>Settings</span>
          </button>
        </div>
      </aside>
      <div className="scrim" onClick={() => setDrawer(false)} aria-hidden="true" />

      <div className="main">
        <header className={`topbar ${scrolled ? 'scrolled' : ''}`}>
          <button type="button" className="icon-btn menu-btn" onClick={() => setDrawer(true)} aria-label="Open menu">
            <Icon name="menu" />
          </button>
          <ReasonerPicker health={stream.health} />
          <div className="topbar-title hide-sm">
            <p>{page.description}</p>
          </div>
          <div className="topbar-status">
            {latest && inOrbit && (
              <span className="topbar-meta hide-sm">MET {fmt.num(mission?.simulated_seconds ?? latest.seq, 0)} s</span>
            )}
            {approval && (
              <a href="#/assurance" style={{ textDecoration: 'none' }}>
                <Pill color={STATUS.warning}>approval</Pill>
              </a>
            )}
            {inOrbit && severity && (
              <span className="hide-sm">
                <Pill color={SEVERITY_COLOR[severity] ?? INK.muted}>{severity}</Pill>
              </span>
            )}
            <Pill color={phaseColor}>{phaseLabel}</Pill>
          </div>
        </header>

        <main className={`page ${page.narrow ? 'narrow' : ''}`}>{children}</main>
        <NoticeBar />
      </div>

      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
    </div>
  )
}
