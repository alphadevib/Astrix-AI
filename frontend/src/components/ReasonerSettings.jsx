// Reasoner settings, shown as a tab of the profile console.
//
// Lists the providers the backend can actually use (a key is set, or Ollama is
// running), switches the active reasoner at runtime, and shows how much of each
// provider's allowance is left so the operator hears about a limit before the
// console quietly falls back to deterministic reasoning.

import { useEffect, useState } from 'react'
import api from '../services/api'
import Icon from './icons'

const TIER_LABEL = { free: 'Free tier', trial: 'Free credits', local: 'Local', paid: 'Paid' }
const TIER_ORDER = ['free', 'trial', 'local', 'paid']
const LEVEL_TAG = { low: 'running low', exhausted: 'out of quota' }

function usageText(usage) {
  if (!usage) return null
  if (usage.requests) return `${usage.requests.remaining} of ${usage.requests.limit} requests left`
  if (usage.daily_limit) return `~${usage.used_today} of ${usage.daily_limit} daily requests used`
  if (usage.used_today) return `${usage.used_today} requests today`
  return null
}

export function UsageAlert({ alert, className = '' }) {
  if (!alert) return null
  return (
    <p className={`form-message usage-message ${alert.level} ${className}`} role="alert">
      <Icon name="warning" size={15} />
      <span>{alert.message}</span>
    </p>
  )
}

export default function ReasonerSettings() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .providers()
        .then((result) => {
          if (cancelled) return
          setData(result)
          setError(null)
        })
        .catch((err) => !cancelled && setError(err.message.replace(/^\d+\s/, '')))
    load()
    // Keep the allowance figures current while the tab is open.
    const timer = window.setInterval(load, 15000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const select = async (provider, chosenModel) => {
    setBusy(`${provider}:${chosenModel ?? ''}`)
    try {
      setData(await api.selectReasoner(provider, chosenModel))
      setError(null)
    } catch (err) {
      setError(err.message.replace(/^\d+\s/, ''))
    } finally {
      setBusy(null)
    }
  }

  if (!data && !error) return <span className="spinner" aria-label="Loading reasoners" />

  const status = data?.status
  const providers = data?.providers ?? []
  const deterministic = !status?.available

  return (
    <>
      <p className="console-note">
        The reasoner answers free-form questions and backs every agent. Agents fall back to deterministic reasoning
        whenever a model fails or runs out of quota.
      </p>
      <UsageAlert alert={status?.alert} />
      {error && (
        <p className="form-message error" role="alert">
          {error}
        </p>
      )}

      <div className="reasoner-list" role="radiogroup" aria-label="Reasoner">
        <button
          type="button"
          role="radio"
          aria-checked={deterministic}
          className="picker-item"
          disabled={busy !== null}
          onClick={() => select('deterministic')}
        >
          <span className="picker-item-main">
            <strong>Deterministic</strong>
            <span>Rule-based reasoners · no network · always available</span>
          </span>
          {deterministic && <Icon name="check" size={16} />}
        </button>

        {TIER_ORDER.map((tier) => {
          const items = providers.filter((p) => p.tier === tier)
          if (!items.length) return null
          return (
            <div key={tier} className="picker-group">
              <div className="picker-group-label">{TIER_LABEL[tier]}</div>
              {items.map((p) => {
                const active = p.active && !deterministic
                const level = p.usage?.level
                const usage = usageText(p.usage)
                return (
                  <div key={p.key} className="picker-provider">
                    <button
                      type="button"
                      role="radio"
                      aria-checked={active}
                      className="picker-item"
                      disabled={busy !== null}
                      onClick={() => select(p.key)}
                      title={p.free_tier}
                    >
                      <span className="picker-item-main">
                        <strong>
                          {p.label}
                          {LEVEL_TAG[level] && <em className={`picker-tag ${level}`}>{LEVEL_TAG[level]}</em>}
                        </strong>
                        <span>
                          {p.default_model}
                          {usage && ` · ${usage}`}
                        </span>
                        {level && level !== 'ok' && p.usage.message && (
                          <span className={`picker-usage ${level}`}>{p.usage.message}</span>
                        )}
                      </span>
                      {active && <Icon name="check" size={16} />}
                    </button>
                    {active && p.models.length > 1 && (
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
                )
              })}
            </div>
          )
        })}
        {providers.length === 0 && (
          <p className="console-note">
            No model provider is configured. Set a key such as <code>GROQ_API_KEY</code> on the backend, or start
            Ollama.
          </p>
        )}
      </div>

      <p className="console-note">
        Keys are read from the backend environment (e.g. <code>GROQ_API_KEY</code>) and never sent to the browser.
        Allowances are what the provider reported to this backend; other apps sharing a key draw on the same limit.
      </p>
    </>
  )
}
