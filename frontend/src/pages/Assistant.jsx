// Astrix — the conversational home of the console.
//
// Commands are executed by the backend's assistant through the same services
// the buttons use; free-form questions go to the active reasoner. The
// conversation survives page switches within the session.

import { useEffect, useRef, useState } from 'react'
import api from '../services/api'
import Icon from '../components/icons'
import Markdown from '../components/Markdown'
import { fmt } from '../components/primitives'

const STORE_KEY = 'astrix.chat.v1'

const SUGGESTIONS = [
  { title: 'Launch a mission', prompt: 'launch mission', note: 'fly the ascent, then monitor on orbit' },
  { title: 'Inject a fault', prompt: 'inject wheel degradation', note: 'once the satellite is on orbit' },
  { title: 'Run an intercept check', prompt: 'run an intercept check with seeker dropout', note: 'trajectory + predicted intercept' },
  { title: 'Design a vehicle', prompt: 'design a 3-stage rocket for a 400 kg imaging satellite to 700 km', note: 'rocket + satellite sizing' },
]

function loadMessages() {
  try {
    return JSON.parse(window.sessionStorage.getItem(STORE_KEY) ?? '[]')
  } catch {
    return []
  }
}

export default function Assistant({ stream, vehicle, navigate, onDesign }) {
  const [messages, setMessages] = useState(() => (loadMessages().length ? loadMessages() : []))
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(messages.slice(-40)))
    } catch {
      /* storage unavailable */
    }
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const send = async (text) => {
    const message = text.trim()
    if (!message || busy) return
    setInput('')
    const history = messages.slice(-10).map(({ role, content }) => ({ role, content }))
    setMessages((list) => [...list, { role: 'user', content: message }])
    setBusy(true)
    try {
      const reply = await api.chat(message, history, vehicle)
      setMessages((list) => [
        ...list,
        { role: 'assistant', content: reply.reply, cards: reply.cards, reasoner: reply.reasoner, navigate: reply.navigate },
      ])
    } catch (error) {
      setMessages((list) => [
        ...list,
        {
          role: 'assistant',
          error: true,
          content: stream.connected
            ? `Request failed: ${error.message}`
            : 'The backend is unreachable. Start it locally (`uvicorn backend.app.main:app`) or set its URL in **Settings**.',
        },
      ])
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const clear = () => {
    setMessages([])
    try {
      window.sessionStorage.removeItem(STORE_KEY)
    } catch {
      /* storage unavailable */
    }
  }

  const empty = messages.length === 0

  return (
    <div className={`chat ${empty ? 'is-empty' : ''}`}>
      {empty ? (
        <div className="chat-hero">
          <img src="/astrix-emblem.svg" alt="" width="44" height="44" />
          <h1>What are we testing today?</h1>
          <p>Launch and stress a spacecraft, check an interceptor's trajectory, design a vehicle or drive a hardware prototype.</p>
        </div>
      ) : (
        <div className="chat-log" aria-live="polite">
          {messages.map((m, i) => (
            <Message key={i} message={m} navigate={navigate} onDesign={onDesign} />
          ))}
          {busy && (
            <div className="msg assistant">
              <div className="msg-avatar">
                <img src="/astrix-emblem.svg" alt="" width="20" height="20" />
              </div>
              <div className="msg-body">
                <span className="typing" aria-label="Astrix is working">
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
      )}

      <div className="composer-wrap">
        {empty && (
          <div className="suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s.title} type="button" className="suggestion" onClick={() => send(s.prompt)}>
                <strong>{s.title}</strong>
                <span>{s.note}</span>
              </button>
            ))}
          </div>
        )}
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault()
            send(input)
          }}
        >
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            placeholder="Message Astrix — try “launch mission” or “help”"
            aria-label="Message Astrix"
            onChange={(event) => {
              setInput(event.target.value)
              event.target.style.height = 'auto'
              event.target.style.height = `${Math.min(200, event.target.scrollHeight)}px`
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                send(input)
              }
            }}
          />
          <div className="composer-actions">
            {!empty && (
              <button type="button" className="composer-ghost" onClick={clear}>
                Clear
              </button>
            )}
            <button type="submit" className="send" disabled={busy || !input.trim()} aria-label="Send">
              <Icon name={busy ? 'stop' : 'send'} size={17} strokeWidth={2.2} />
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Message({ message, navigate, onDesign }) {
  if (message.role === 'user') {
    return (
      <div className="msg user">
        <div className="msg-bubble">{message.content}</div>
      </div>
    )
  }
  return (
    <div className={`msg assistant ${message.error ? 'error' : ''}`}>
      <div className="msg-avatar">
        <img src="/astrix-emblem.svg" alt="" width="20" height="20" />
      </div>
      <div className="msg-body">
        <Markdown text={message.content} />
        {message.cards?.map((card, i) => (
          <Card key={i} card={card} navigate={navigate} onDesign={onDesign} />
        ))}
        {message.navigate && (
          <button type="button" className="link-btn" onClick={() => navigate(message.navigate)}>
            Open {message.navigate === 'assurance' ? 'Flight Assurance' : message.navigate === 'hardware' ? 'Hardware Link' : message.navigate} →
          </button>
        )}
        {message.reasoner && (
          <div className="msg-meta">
            {message.reasoner.kind === 'llm' ? `${message.reasoner.provider} · ${message.reasoner.model}` : 'deterministic'}
          </div>
        )}
      </div>
    </div>
  )
}

function Card({ card, navigate, onDesign }) {
  const { kind, data } = card
  if (kind === 'design') {
    const a = data.analysis
    const d = data.design
    return (
      <div className="result-card">
        <div className="result-grid">
          <Stat label="Verdict" value={a.verdict} tone={a.feasible ? 'good' : 'bad'} />
          <Stat label="Gross mass" value={`${fmt.num(a.gross_mass_t, 1)} t`} />
          <Stat label="Δv margin" value={`${fmt.num(a.margin_ms, 0)} m/s`} />
          <Stat label="Payload" value={`${fmt.num(d.satellite.mass_kg, 0)} kg`} />
        </div>
        {a.warnings?.length > 0 && <p className="result-warn">{a.warnings[0]}</p>}
        <div className="result-actions">
          <button
            type="button"
            className="btn"
            onClick={() => {
              onDesign(d)
              navigate('studio')
            }}
          >
            Open in Vehicle Studio
          </button>
        </div>
      </div>
    )
  }
  if (kind === 'intercept') {
    const { outcome, preflight } = data
    return (
      <div className="result-card">
        <div className="result-grid">
          <Stat label="Pre-flight" value={preflight.go ? 'GO' : 'NO-GO'} tone={preflight.go ? 'good' : 'bad'} />
          <Stat label="Outcome" value={outcome.result} tone={outcome.result === 'INTERCEPT' ? 'good' : 'bad'} />
          <Stat label="Miss distance" value={outcome.miss_km == null ? '—' : `${fmt.num(outcome.miss_km * 1000, 0)} m`} />
          <Stat label="Intercept at" value={outcome.t == null ? '—' : `t+${fmt.num(outcome.t, 1)} s`} />
        </div>
        <div className="result-actions">
          <button type="button" className="btn" onClick={() => navigate('intercept')}>
            Open Intercept Lab
          </button>
        </div>
      </div>
    )
  }
  if (kind === 'training' && data.metrics) {
    return (
      <div className="result-card">
        <div className="result-grid">
          <Stat label="Version" value={data.version} />
          <Stat label="Examples" value={data.examples} />
          {Object.entries(data.metrics).map(([head, m]) => (
            <Stat key={head} label={fmt.title(head)} value={m.accuracy == null ? 'n/a' : fmt.pct(m.accuracy)} />
          ))}
        </div>
      </div>
    )
  }
  return null
}

function Stat({ label, value, tone }) {
  return (
    <div className={`stat ${tone ?? ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
