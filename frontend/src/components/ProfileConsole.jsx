// The profile console: the account, its security, and which backend this
// browser talks to. Replaces the old Settings dialog and its pasted API key —
// the session is the only credential now.

import { useEffect, useState } from 'react'
import api, { apiBase, connection } from '../services/api'
import session from '../services/auth'
import Icon from './icons'

const TABS = [
  ['profile', 'Profile'],
  ['security', 'Security'],
  ['connection', 'Connection'],
]

export function initials(user) {
  const source = (user?.name || user?.email || '?').trim()
  const words = source.split(/[\s@._-]+/).filter(Boolean)
  return (words.length > 1 ? words[0][0] + words[1][0] : source.slice(0, 2)).toUpperCase()
}

function errorText(error) {
  const text = String(error?.message ?? error).replace(/^\d{3}\s+/, '')
  return text === 'Failed to fetch' ? 'The backend is unreachable.' : text
}

export default function ProfileConsole({ user, onUserUpdated, onClose }) {
  const [tab, setTab] = useState('profile')

  useEffect(() => {
    const onKey = (event) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal console-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="console-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="icon-btn modal-close" onClick={onClose} aria-label="Close">
          <Icon name="close" />
        </button>
        <div className="console-head">
          <span className="avatar" aria-hidden="true">
            {initials(user)}
          </span>
          <div>
            <h2 id="console-title">{user.name || 'Your account'}</h2>
            <p>{user.email}</p>
          </div>
        </div>
        <div className="console-tabs" role="tablist">
          {TABS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              className="console-tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="console-body" role="tabpanel">
          {tab === 'profile' && <ProfileTab user={user} onUserUpdated={onUserUpdated} />}
          {tab === 'security' && <SecurityTab />}
          {tab === 'connection' && <ConnectionTab />}
        </div>
      </div>
    </div>
  )
}

function ProfileTab({ user, onUserUpdated }) {
  const [form, setForm] = useState({
    name: user.name ?? '',
    organisation: user.organisation ?? '',
    role: user.role ?? '',
  })
  const [state, setState] = useState(null)
  const update = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }))

  const save = async (event) => {
    event.preventDefault()
    setState({ busy: true })
    try {
      const result = await api.updateProfile({
        name: form.name.trim(),
        organisation: form.organisation.trim(),
        role: form.role.trim(),
      })
      onUserUpdated(result.user)
      setState({ ok: 'Profile saved.' })
    } catch (error) {
      setState({ error: errorText(error) })
    }
  }

  return (
    <form onSubmit={save} style={{ display: 'grid', gap: 4 }}>
      <label className="stack-field">
        <span>Name</span>
        <input type="text" value={form.name} onChange={update('name')} maxLength={120} autoComplete="name" />
      </label>
      <div className="field-row">
        <label className="stack-field">
          <span>Organisation</span>
          <input
            type="text"
            value={form.organisation}
            onChange={update('organisation')}
            maxLength={160}
            autoComplete="organization"
          />
        </label>
        <label className="stack-field">
          <span>Role</span>
          <input type="text" value={form.role} onChange={update('role')} maxLength={64} />
        </label>
      </div>
      <label className="stack-field">
        <span>Email</span>
        <input type="email" value={user.email} disabled />
      </label>
      <Message state={state} />
      <div className="console-actions">
        <button type="submit" className="btn primary" disabled={state?.busy}>
          Save profile
        </button>
      </div>
    </form>
  )
}

function SecurityTab() {
  const [form, setForm] = useState({ current: '', next: '' })
  const [state, setState] = useState(null)
  const [sessions, setSessions] = useState(null)
  const update = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }))

  const loadSessions = () =>
    api
      .sessions()
      .then((r) => setSessions(r.sessions))
      .catch(() => setSessions([]))

  useEffect(() => {
    loadSessions()
  }, [])

  const change = async (event) => {
    event.preventDefault()
    setState({ busy: true })
    try {
      // Keep this device signed in; the server ends every other session.
      await api.changePassword(form.current, form.next, false)
      setForm({ current: '', next: '' })
      setState({ ok: 'Password changed. Every other device has been signed out.' })
      loadSessions()
    } catch (error) {
      setState({ error: errorText(error) })
    }
  }

  const signOutEverywhere = async () => {
    try {
      await api.logoutAll()
    } finally {
      session.set('')
    }
  }

  return (
    <>
      <form onSubmit={change} style={{ display: 'grid', gap: 4 }}>
        <div className="field-row">
          <label className="stack-field">
            <span>Current password</span>
            <input
              type="password"
              value={form.current}
              onChange={update('current')}
              autoComplete="current-password"
            />
          </label>
          <label className="stack-field">
            <span>New password</span>
            <input type="password" value={form.next} onChange={update('next')} autoComplete="new-password" />
          </label>
        </div>
        <p className="console-note">At least 10 characters. Changing it signs out every other device.</p>
        <Message state={state} />
        <div className="console-actions">
          <button type="submit" className="btn primary" disabled={state?.busy || !form.current || !form.next}>
            Change password
          </button>
        </div>
      </form>

      <div style={{ display: 'grid', gap: 8 }}>
        <strong style={{ fontSize: 13.5 }}>Active sessions</strong>
        {sessions === null && <span className="spinner" aria-label="Loading sessions" />}
        {sessions?.map((s) => (
          <div key={s.id} className="session-row">
            <div style={{ minWidth: 0 }}>
              <strong>{describeAgent(s.user_agent)}</strong>
              <div>Last active {utc(s.last_seen_at).toLocaleString()}</div>
            </div>
            {s.current && <span className="tag">This device</span>}
          </div>
        ))}
      </div>
      <div className="console-actions">
        <button type="button" className="btn" onClick={signOutEverywhere}>
          Sign out everywhere
        </button>
      </div>
    </>
  )
}

// The server stores naive UTC timestamps; without a zone JS would read them as local.
function utc(timestamp) {
  return new Date(/(Z|[+-]\d\d:\d\d)$/.test(timestamp) ? timestamp : `${timestamp}Z`)
}

function describeAgent(agent = '') {
  if (!agent) return 'Unknown device'
  const browser = /Edg\//.test(agent)
    ? 'Edge'
    : /Chrome\//.test(agent)
      ? 'Chrome'
      : /Firefox\//.test(agent)
        ? 'Firefox'
        : /Safari\//.test(agent)
          ? 'Safari'
          : 'Browser'
  const os = /Windows/.test(agent)
    ? 'Windows'
    : /Mac OS X/.test(agent)
      ? 'macOS'
      : /Android/.test(agent)
        ? 'Android'
        : /iPhone|iPad/.test(agent)
          ? 'iOS'
          : /Linux/.test(agent)
            ? 'Linux'
            : ''
  return os ? `${browser} on ${os}` : browser
}

function ConnectionTab() {
  const [base, setBase] = useState(apiBase())
  const [state, setState] = useState(null)

  const test = async () => {
    const previous = apiBase()
    connection.saveBase(base)
    setState({ busy: true })
    try {
      const meta = await api.meta()
      setState({ ok: `Connected to ${meta.app} ${meta.version}.` })
    } catch (error) {
      setState({ error: errorText(error) })
    } finally {
      connection.saveBase(previous)
    }
  }

  const save = () => {
    connection.saveBase(base)
    // Sessions belong to a backend; a different one needs a fresh sign-in.
    if (base.trim().replace(/\/$/, '') !== apiBase()) session.set('')
    window.location.reload()
  }

  return (
    <>
      <p className="console-note">
        Point this console at an Astrix backend. Leave it empty to use the same origin. Changing it signs you out,
        since accounts live on the backend.
      </p>
      <label className="stack-field">
        <span>Backend URL</span>
        <input
          type="text"
          placeholder="https://astrix-api.example.com"
          value={base}
          onChange={(event) => setBase(event.target.value)}
          autoComplete="url"
        />
      </label>
      <Message state={state} />
      <div className="console-actions">
        <button type="button" className="btn" onClick={test} disabled={state?.busy}>
          Test
        </button>
        <button type="button" className="btn primary" onClick={save}>
          Save and reload
        </button>
      </div>
    </>
  )
}

function Message({ state }) {
  if (!state || state.busy) return null
  return (
    <p className={`form-message ${state.error ? 'error' : 'ok'}`} role={state.error ? 'alert' : 'status'}>
      {state.error ?? state.ok}
    </p>
  )
}
