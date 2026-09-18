// Sign in / create an account.
//
// The console has no anonymous mode: every route and the telemetry socket
// need a session. This page is the only thing a signed-out visitor sees.

import { useEffect, useState } from 'react'
import api from '../services/api'
import session from '../services/auth'
import AstrixOrb from '../components/AstrixOrb'
import { NOTICE_SHORT } from '../components/Disclaimer'

export default function Auth({ onSignedIn, reason }) {
  const [mode, setMode] = useState('login')
  const [registrationOpen, setRegistrationOpen] = useState(true)
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .meta()
      .then((meta) => setRegistrationOpen(meta.registration_open !== false))
      .catch(() => {
        /* backend unreachable — the submit will say so */
      })
  }, [])

  const creating = mode === 'register'
  const update = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const result = creating
        ? await api.register({ name: form.name.trim(), email: form.email.trim(), password: form.password })
        : await api.login(form.email.trim(), form.password)
      session.set(result.token)
      onSignedIn(result.user)
    } catch (err) {
      // "401 Email or password is incorrect." → drop the status code.
      const text = String(err.message ?? err).replace(/^\d{3}\s+/, '')
      setError(text === 'Failed to fetch' ? 'The backend is unreachable. Check that it is running.' : text)
    } finally {
      setBusy(false)
    }
  }

  const switchMode = () => {
    setMode(creating ? 'login' : 'register')
    setError('')
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-head">
          <AstrixOrb size={72} state={busy ? 'thinking' : 'idle'} />
          <h1>
            {creating ? (
              <>
                Create your <span className="mark-hl">account</span>
              </>
            ) : (
              <>
                Sign in to <span className="mark-hl">Astrix</span>
              </>
            )}
          </h1>
          <p>
            {creating
              ? 'Your conversations, missions and preferences stay with your account.'
              : 'Spacecraft assurance, intercept testing and hardware in the loop.'}
          </p>
        </div>

        {reason && !error && <p className="form-message error">{reason}</p>}

        <form className="auth-form" onSubmit={submit} noValidate>
          {creating && (
            <label className="stack-field">
              <span>Name</span>
              <input type="text" value={form.name} onChange={update('name')} autoComplete="name" maxLength={120} />
            </label>
          )}
          <label className="stack-field">
            <span>Email</span>
            <input
              type="email"
              value={form.email}
              onChange={update('email')}
              autoComplete="email"
              required
              autoFocus
            />
          </label>
          <label className="stack-field">
            <span>Password</span>
            <input
              type="password"
              value={form.password}
              onChange={update('password')}
              autoComplete={creating ? 'new-password' : 'current-password'}
              required
            />
          </label>
          {creating && <p className="password-hint">At least 10 characters, and not built from your name or email.</p>}
          {error && (
            <p className="form-message error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn primary" disabled={busy || !form.email || !form.password}>
            {busy ? <span className="spinner" aria-hidden="true" /> : creating ? 'Create account' : 'Sign in'}
          </button>
        </form>

        {(registrationOpen || creating) && (
          <div className="auth-switch">
            <span>{creating ? 'Already have an account?' : 'New to Astrix?'}</span>
            <button type="button" className="link-button" onClick={switchMode}>
              {creating ? 'Sign in' : 'Create an account'}
            </button>
          </div>
        )}

        <p className="auth-foot">{NOTICE_SHORT}</p>
      </div>
    </div>
  )
}
