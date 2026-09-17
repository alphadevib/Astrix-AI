// Connection settings: which backend this console talks to, and its API token.
//
// A Vercel-hosted console usually talks to a backend on another origin. The
// build can bake that in with VITE_API_BASE; this dialog lets an operator point
// a deployed console at their own backend without rebuilding.

import { useState } from 'react'
import api, { connection } from '../services/api'
import Icon from './icons'

export default function SettingsDialog({ onClose }) {
  const [base, setBase] = useState(connection.base())
  const [token, setToken] = useState(connection.token())
  const [test, setTest] = useState(null)

  const save = () => {
    connection.save({ base, token })
    // The WebSocket and every open request use the old origin; reload cleanly.
    window.location.reload()
  }

  const check = async () => {
    const previous = { base: connection.base(), token: connection.token() }
    connection.save({ base, token })
    setTest({ state: 'pending' })
    try {
      const meta = await api.meta()
      setTest({ state: 'ok', text: `Connected to ${meta.app} ${meta.version}${meta.auth_required ? ' · token required' : ''}` })
    } catch (error) {
      setTest({ state: 'error', text: error.message })
    } finally {
      connection.save(previous)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="icon-btn modal-close" onClick={onClose} aria-label="Close">
          <Icon name="close" />
        </button>
        <h2 id="settings-title">Connection</h2>
        <p>Point this console at an Astrix-AI backend. Leave the URL empty to use the same origin.</p>
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
        <label className="stack-field">
          <span>API token</span>
          <input
            type="password"
            placeholder="Only if the backend sets ASTRIX_API_TOKEN"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="off"
          />
        </label>
        {test && (
          <p className={`test-result ${test.state}`} role="status">
            {test.state === 'pending' ? 'Checking…' : test.text}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={check}>
            Test
          </button>
          <button type="button" className="btn primary" onClick={save}>
            Save and reload
          </button>
        </div>
      </div>
    </div>
  )
}
