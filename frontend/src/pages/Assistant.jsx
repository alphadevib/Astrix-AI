// Astrix — the conversational home of the console.
//
// Commands are executed by the backend's assistant through the same services
// the buttons use; free-form questions go to the active reasoner. Every
// conversation is a server-side thread owned by the signed-in account: this
// component shows one thread, or an empty one that the server creates on the
// first message.

import { useEffect, useRef, useState } from 'react'
import api from '../services/api'
import Icon from '../components/icons'
import Markdown from '../components/Markdown'
import AstrixMark from '../components/AstrixMark'
import AstrixOrb from '../components/AstrixOrb'
import { fmt } from '../components/primitives'

const SUGGESTIONS = [
  { title: 'Launch a mission', prompt: 'launch mission', note: 'fly the ascent, then monitor on orbit' },
  { title: 'Inject a fault', prompt: 'inject wheel degradation', note: 'once the satellite is on orbit' },
  { title: 'Run an intercept check', prompt: 'run an intercept check with seeker dropout', note: 'trajectory + predicted intercept' },
  { title: 'Design a vehicle', prompt: 'design a 3-stage rocket for a 400 kg imaging satellite to 700 km', note: 'rocket + satellite sizing' },
]

// Before threads lived on the server, the chat was one sessionStorage blob —
// which is why "New conversation" kept bringing the old one back.
try {
  window.sessionStorage.removeItem('astrix.chat.v1')
} catch {
  /* storage unavailable */
}

export default function Assistant({ conversationId, onThreadCreated, onTurn, onMissing, stream, vehicle, navigate, onDesign }) {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(Boolean(conversationId))
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const threadRef = useRef(conversationId)
  const endRef = useRef(null)
  const inputRef = useRef(null)

  // The parent remounts this component to switch threads, so the id it was
  // mounted with is the only one that ever needs loading.
  useEffect(() => {
    if (!conversationId) return undefined
    let cancelled = false
    api
      .conversation(conversationId)
      .then((result) => {
        if (!cancelled) setMessages(result.conversation.messages ?? [])
      })
      .catch(() => {
        if (!cancelled) onMissing?.()
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const send = async (text) => {
    const message = text.trim()
    if (!message || busy) return
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
    const history = messages.slice(-10).map(({ role, content }) => ({ role, content }))
    setMessages((list) => [...list, { role: 'user', content: message }])
    setBusy(true)
    try {
      const reply = await api.chat(message, threadRef.current, history, vehicle)
      if (!threadRef.current && reply.conversation_id) {
        threadRef.current = reply.conversation_id
        onThreadCreated?.(reply.conversation_id)
      } else {
        onTurn?.()
      }
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
            : 'The backend is unreachable. Start it locally (`uvicorn backend.app.main:app`) or set its URL under **Profile and settings → Connection**.',
        },
      ])
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const empty = messages.length === 0 && !loading

  return (
    <div className={`chat ${empty ? 'is-empty' : ''}`}>
      {empty ? (
        <div className="chat-hero">
          <AstrixOrb size={84} state={busy ? 'thinking' : 'idle'} />
          <h1>
            What are we <span className="mark-hl">testing</span> today?
          </h1>
          <p>Launch and stress a spacecraft, check an interceptor's trajectory, design a vehicle or drive a hardware prototype.</p>
        </div>
      ) : (
        <div className="chat-log" aria-live="polite" aria-busy={loading}>
          {loading && (
            <div className="page-loading" role="status">
              <span className="spinner" aria-hidden="true" /> Loading conversation…
            </div>
          )}
          {messages.map((m, i) => (
            <Message key={i} message={m} navigate={navigate} onDesign={onDesign} />
          ))}
          {busy && (
            <div className="msg assistant">
              <div className="msg-avatar">
                <AstrixMark size={18} tone="nebula" />
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
            <button type="submit" className="send" disabled={busy || loading || !input.trim()} aria-label="Send">
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
        <AstrixMark size={18} tone="nebula" />
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
