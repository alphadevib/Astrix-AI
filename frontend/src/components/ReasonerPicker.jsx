// Reasoner selector, in the spirit of an AI workspace's model picker.
//
// Lists the providers the backend can actually use (a key is set, or Ollama is
// running) and switches the active reasoner at runtime.

import { useEffect, useRef, useState } from 'react'
import api from '../services/api'
import Icon from './icons'

const TIER_LABEL = { free: 'Free tier', trial: 'Free credits', local: 'Local', paid: 'Paid' }
const TIER_ORDER = ['free', 'trial', 'local', 'paid']

export default function ReasonerPicker({ health }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const ref = useRef(null)

  const load = () =>
    api
      .providers()
      .then((result) => {
        setData(result)
        setError(null)
      })
      .catch((err) => setError(err.message))

  useEffect(() => {
    load()
  }, [health?.llm?.provider])

  useEffect(() => {
    if (!open) return undefined
    load()
    const onDown = (event) => {
      if (ref.current && !ref.current.contains(event.target)) setOpen(false)
    }
    const onKey = (event) => event.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const status = data?.status ?? health?.llm
  const label = status?.available ? status.provider_label ?? status.provider : 'Deterministic'
  const model = status?.available ? status.model : 'rule-based reasoners'

  const select = async (provider, chosenModel) => {
    setBusy(`${provider}:${chosenModel ?? ''}`)
    try {
      const result = await api.selectReasoner(provider, chosenModel)
      setData(result)
      setError(null)
      setOpen(false)
    } catch (err) {
      setError(err.message.replace(/^\d+\s/, ''))
    } finally {
      setBusy(null)
    }
  }

  const providers = data?.providers ?? []

  return (
    <div className="picker" ref={ref}>
      <button
        type="button"
        className="picker-button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title="Choose the reasoner"
      >
        <span className="picker-label">{label}</span>
        <span className="picker-model">{model}</span>
        <Icon name="chevron" size={14} />
      </button>

      {open && (
        <div className="picker-menu" role="menu">
          <div className="picker-head">
            <strong>Reasoner</strong>
            <span>Agents fall back to deterministic reasoning whenever a model fails.</span>
          </div>
          {error && <p className="picker-error">{error}</p>}

          <button
            type="button"
            role="menuitemradio"
            aria-checked={!status?.available}
            className="picker-item"
            onClick={() => select('deterministic')}
          >
            <span className="picker-item-main">
              <strong>Deterministic</strong>
              <span>Rule-based reasoners · no network · always available</span>
            </span>
            {!status?.available && <Icon name="check" size={16} />}
          </button>

          {TIER_ORDER.map((tier) => {
            const items = providers.filter((p) => p.tier === tier)
            if (!items.length) return null
            return (
              <div key={tier} className="picker-group">
                <div className="picker-group-label">{TIER_LABEL[tier]}</div>
                {items.map((p) => (
                  <div key={p.key} className="picker-provider">
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={p.active && status?.available}
                      className="picker-item"
                      disabled={busy !== null}
                      onClick={() => select(p.key)}
                      title={p.free_tier}
                    >
                      <span className="picker-item-main">
                        <strong>
                          {p.label}
                          {p.benched && <em className="picker-tag">cooling down</em>}
                        </strong>
                        <span>{p.default_model}</span>
                      </span>
                      {p.active && status?.available && <Icon name="check" size={16} />}
                    </button>
                    {p.active && status?.available && p.models.length > 1 && (
                      <div className="picker-models">
                        {p.models.map((m) => (
                          <button
                            key={m}
                            type="button"
                            className={`choice ${m === status.model ? 'active' : ''}`}
                            disabled={busy !== null}
                            onClick={() => select(p.key, m)}
                          >
                            {m}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          })}
          <p className="picker-foot">
            Keys are read from the backend environment (e.g. <code>GROQ_API_KEY</code>) and never sent to the browser.
          </p>
        </div>
      )}
    </div>
  )
}
