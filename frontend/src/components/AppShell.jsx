// Application frame: collapsible sidebar, quiet top bar, and the standing
// results notice. Modelled on agentic AI workspaces — the operator's attention
// belongs on the content; chrome stays out of the way.
//
// The top bar still carries what an operator must never lose sight of: mission
// phase, Astrix's current severity, pending approvals and whether the feed is live.
// The sidebar carries the operator's conversation threads and their account.

import { useEffect, useRef, useState } from 'react'
import { SEVERITY_COLOR, STATUS, INK } from '../theme'
import Icon from './icons'
import { Pill, fmt } from './primitives'
import ProfileConsole, { initials } from './ProfileConsole'
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

function ThreadList({ threads, activeId, onSelect, onDelete }) {
  if (threads === null) return null
  if (threads.length === 0) {
    return <div className="thread-empty">No conversations yet. Ask Astrix anything to start one.</div>
  }
  return (
    <div className="thread-list" role="list" aria-label="Conversations">
      {threads.map((thread) => (
        <div
          key={thread.id}
          role="listitem"
          tabIndex={0}
          className={`thread-row ${thread.id === activeId ? 'active' : ''}`}
          aria-current={thread.id === activeId ? 'true' : undefined}
          title={thread.title}
          onClick={() => onSelect(thread.id)}
          onKeyDown={(event) => {
            if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
              event.preventDefault()
              onSelect(thread.id)
            }
          }}
        >
          <span className="thread-title">{thread.title}</span>
          <button
            type="button"
            className="thread-delete"
            aria-label={`Delete conversation: ${thread.title}`}
            title="Delete conversation"
            onClick={(event) => {
              event.stopPropagation()
              onDelete(thread.id)
            }}
          >
            <Icon name="trash" size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

// Shown whenever the active reasoner's allowance is low or every reasoner is
// out of quota; opens the reasoner settings where another can be picked.
function UsageWarning({ alert, onOpen }) {
  if (!alert) return null
  const exhausted = alert.level === 'exhausted'
  return (
    <button
      type="button"
      className={`usage-warning ${alert.level}`}
      onClick={onOpen}
      title={alert.message}
      aria-label={`${exhausted ? 'Reasoner out of quota' : 'Reasoner limit running low'}: ${alert.message}`}
    >
      <Icon name="warning" size={15} />
      <span>{exhausted ? 'Reasoner out of quota' : 'Reasoner limit low'}</span>
    </button>
  )
}

function AccountChip({ user, onOpenProfile, onOpenReasoner, onSignOut }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (event) => {
      if (!ref.current?.contains(event.target)) setOpen(false)
    }
    const onKey = (event) => event.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref}>
      {open && (
        <div className="account-menu" role="menu">
          <div className="account-menu-head">
            <strong>{user.name || 'Operator'}</strong>
            <span>{user.email}</span>
          </div>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setOpen(false)
              onOpenProfile()
            }}
          >
            <Icon name="user" size={16} />
            Profile and settings
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setOpen(false)
              onOpenReasoner()
            }}
          >
            <Icon name="model" size={16} />
            Reasoner
          </button>
          <button type="button" role="menuitem" className="menu-item danger" onClick={onSignOut}>
            <Icon name="logout" size={16} />
            Sign out
          </button>
        </div>
      )}
      <button
        type="button"
        className="account-chip"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title={user.email}
      >
        <span className="avatar" aria-hidden="true">
          {initials(user)}
        </span>
        <span className="account-meta">
          <span className="account-name">{user.name || user.email}</span>
          <span className="account-role">{user.organisation || user.role || user.email}</span>
        </span>
      </button>
    </div>
  )
}

export default function AppShell({
  pages,
  current,
  stream,
  user,
  onUserUpdated,
  onSignOut,
  threads,
  activeThread,
  onSelectThread,
  onDeleteThread,
  onNewChat,
  children,
}) {
  const { mission, latest, detection, approval } = stream
  const page = pages.find((p) => p.key === current) ?? pages[0]
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [drawer, setDrawer] = useState(false)
  // null, or the profile console tab to open.
  const [profile, setProfile] = useState(null)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => setDrawer(false), [current, activeThread])

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
          <a className="brand" href="/" title="Astrix home">
            <span className="brand-name">Astrix</span>
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
          <div className="thread-group">
            <div className="nav-label">Conversations</div>
            <ThreadList threads={threads} activeId={activeThread} onSelect={onSelectThread} onDelete={onDeleteThread} />
          </div>
        </nav>

        <div className="sidebar-foot">
          <AccountChip
            user={user}
            onOpenProfile={() => setProfile('profile')}
            onOpenReasoner={() => setProfile('reasoner')}
            onSignOut={onSignOut}
          />
        </div>
      </aside>
      <div className="scrim" onClick={() => setDrawer(false)} aria-hidden="true" />

      <div className="main">
        <header className={`topbar ${scrolled ? 'scrolled' : ''}`}>
          <button type="button" className="icon-btn menu-btn" onClick={() => setDrawer(true)} aria-label="Open menu">
            <Icon name="menu" />
          </button>
          <div className="topbar-title hide-sm">
            <p>{page.description}</p>
          </div>
          <div className="topbar-status">
            <UsageWarning alert={stream.health?.llm?.alert} onOpen={() => setProfile('reasoner')} />
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
          </div>
        </header>

        <main className={`page ${page.narrow ? 'narrow' : ''}`}>{children}</main>
        <NoticeBar />
      </div>

      {profile && (
        <ProfileConsole
          key={profile}
          user={user}
          initialTab={profile}
          onUserUpdated={onUserUpdated}
          onClose={() => setProfile(null)}
        />
      )}
    </div>
  )
}
