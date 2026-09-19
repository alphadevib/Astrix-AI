// Public landing page for Astrix. Deliberately dependency-free (no charts,
// no WebSocket) so it is the fastest thing on the domain; the console loads as
// a separate chunk from /app.

import { useEffect, useState } from 'react'
import '../landing.css'
import AstrixMark from '../components/AstrixMark'

const LOOP = ['Detect', 'Remember', 'Diagnose', 'Assess', 'Plan', 'Simulate', 'Verify', 'Recover', 'Learn']

const CAPABILITIES = [
  {
    title: 'Flight Assurance',
    body: 'Fly the ascent, stress it with launch faults, then inject on-orbit anomalies and watch Astrix detect, diagnose, plan and recover.',
    icon: 'M3 12h3l2.5-6 4 12 2.5-6H21',
  },
  {
    title: 'Intercept Lab',
    body: 'Check an interceptor’s trajectory and predicted intercept point, then inject seeker, guidance or cyber faults.',
    icon: 'M12 4a8 8 0 100 16 8 8 0 000-16zM12 9a3 3 0 100 6 3 3 0 000-6zM12 1v4M12 19v4M1 12h4M19 12h4',
  },
  {
    title: 'Vehicle Studio',
    body: 'Describe a rocket and satellite in plain English or edit every stage. Get Δv, thrust-to-weight and power budgets, then fly it.',
    icon: 'M12 2c3 2.5 4.5 6 4.5 10.5V17h-9v-4.5C7.5 8 9 4.5 12 2zM7.5 14L5 17v3l2.5-1.5M16.5 14l2.5 3v3l-2.5-1.5',
  },
  {
    title: 'Astrix-LM',
    body: 'Every verified decision becomes an encrypted, hash-chained training example for Astrix’s own on-board model.',
    icon: 'M6 4a2 2 0 100 4 2 2 0 000-4zM18 4a2 2 0 100 4 2 2 0 000-4zM6 16a2 2 0 100 4 2 2 0 000-4zM18 16a2 2 0 100 4 2 2 0 000-4zM12 9.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z',
  },
  {
    title: 'Switchable reasoners',
    body: 'Gemini, Groq, Hugging Face or a local Ollama model, switched at runtime, with a deterministic fallback when a model fails.',
    icon: 'M12 3l1.8 4.6L18.5 9l-4.7 1.5L12 15l-1.8-4.5L5.5 9l4.7-1.4L12 3z',
  },
]

// Only what the backend's provider registry actually ships.
const PROVIDERS = [
  { name: 'Gemini', note: 'Google' },
  { name: 'Groq', note: 'LPU inference' },
  { name: 'Hugging Face', note: 'Inference API' },
  { name: 'Ollama', note: 'local, air-gapped' },
  { name: 'Deterministic', note: 'always on' },
]

function Glyph({ d, size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

const ARROW = 'M5 12h14M13 6l6 6-6 6'
const WARNING = 'M10.3 3.9L2.4 17.5A2 2 0 004.1 20.5h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4.5M12 17h.01'

// A still of the console: enough to show what signing in leads to, drawn in
// markup so it stays crisp and costs nothing to load.
function ConsolePreview() {
  return (
    <div className="l-preview" aria-hidden="true">
      <div className="l-window">
        <aside className="l-window-side">
          <div className="l-window-brand">
            <AstrixMark size={18} tone="nebula" />
            <span>Astrix</span>
          </div>
          <span className="l-window-new">+ New conversation</span>
          {['Astrix', 'Flight Assurance', 'Intercept Lab', 'Vehicle Studio'].map((label, i) => (
            <span key={label} className={`l-window-nav ${i === 1 ? 'on' : ''}`}>
              {label}
            </span>
          ))}
        </aside>
        <div className="l-window-main">
          <div className="l-window-top">
            <span className="l-chip">Gemini · flash</span>
            <span className="l-chip good">● on orbit</span>
          </div>
          <div className="l-msg user">inject wheel degradation</div>
          <div className="l-msg bot">
            <strong>Reaction wheel #3 degradation detected.</strong> Vibration and motor current rising together;
            corroborated by pointing error. Recovery <code>isolate_wheel_3</code> passed the safety engine and the
            digital twin (48/50 Monte Carlo runs contained).
          </div>
          <div className="l-window-grid">
            <div>
              <span>Severity</span>
              <strong className="warn">WARNING</strong>
            </div>
            <div>
              <span>Detect → alarm</span>
              <strong>4.2 s</strong>
            </div>
            <div>
              <span>Twin confidence</span>
              <strong className="good">96%</strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Landing() {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <div className={`landing ${scrolled ? 'scrolled' : ''}`}>
      <header className="l-nav">
        <a className="l-brand" href="/">
          <AstrixMark size={28} tone="nebula" />
          <span>Astrix</span>
        </a>
        <nav aria-label="Sections">
          <a href="#capabilities">Capabilities</a>
          <a href="#astrix-lm">Astrix-LM</a>
          <a href="#safety">Safety</a>
        </nav>
        <div className="l-nav-actions">
          <a className="l-btn ghost small" href="/app">
            Sign in
          </a>
          <a className="l-btn primary small" href="/app">
            Open console
          </a>
        </div>
      </header>

      <main>
        <section className="l-hero">
          <div className="l-horizon" aria-hidden="true" />
          <a className="l-pill" href="#capabilities">
            <span className="l-dot" />
            Detect · Reason · Recover · Learn
          </a>
          <h1>Find the failure before the flight does.</h1>
          <p className="l-lead">
            Astrix launches, stresses and recovers simulated spacecraft, checks interceptor trajectories, and sizes custom
            rockets — with an AI loop that detects, reasons, verifies and learns.
          </p>
          <div className="l-cta">
            <a className="l-btn primary" href="/app">
              Open the console <Glyph d={ARROW} size={17} />
            </a>
            <a className="l-btn ghost" href="#capabilities">
              See what it does
            </a>
          </div>
          <ConsolePreview />
        </section>

        <section className="l-loop" aria-label="The Astrix loop">
          {LOOP.map((step, i) => (
            <span key={step} className="l-loop-step">
              <span className="l-loop-index">{String(i + 1).padStart(2, '0')}</span>
              {step}
            </span>
          ))}
        </section>

        <section id="capabilities" className="l-section">
          <div className="l-section-head">
            <h2>
              Every stage of the <span className="l-hl">campaign</span>
            </h2>
            <p>Operate it with buttons or in plain language. The assistant runs the same verified actions as the buttons.</p>
          </div>
          <div className="l-features">
            {CAPABILITIES.map((c) => (
              <article key={c.title} className="l-feature">
                <div className="l-feature-icon">
                  <Glyph d={c.icon} />
                </div>
                <h3>{c.title}</h3>
                <p>{c.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="l-section" id="astrix-lm">
          <div className="l-section-head">
            <h2>
              A model that <span className="l-hl">learns</span>
            </h2>
            <p>
              Deterministic reasoners and a safety engine produce decisions you can audit. Astrix captures them securely and
              keeps training its own small model, scoring it in shadow against live decisions until it has earned trust.
            </p>
          </div>
          <div className="l-stats">
            {[
              ['Fernet', 'encrypted at rest'],
              ['SHA-256', 'hash-chained corpus'],
              ['Shadow', 'agreement scoring'],
              ['LoRA', 'small-LLM fine-tuning'],
            ].map(([value, label]) => (
              <div key={value}>
                <strong>{value}</strong>
                <span>{label}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="l-section" aria-label="Reasoners">
          <div className="l-panel">
            <div className="l-panel-copy">
              <span className="l-badge">
                <b>NEW</b> Pick a reasoner at runtime
              </span>
              <h2>Bring a free model, or none at all.</h2>
              <p>Every agent has a deterministic fallback. Add a free API key and switch reasoners from the console.</p>
              <a className="l-btn outline" href="/app">
                Open the console <Glyph d={ARROW} size={16} />
              </a>
            </div>
            <div className="l-providers">
              {PROVIDERS.map((p) => (
                <div key={p.name} className="l-provider">
                  <strong>{p.name}</strong>
                  <span>{p.note}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="l-section" id="safety">
          <div className="l-safety">
            <div className="l-safety-icon">
              <Glyph d={WARNING} />
            </div>
            <div>
              <h2>Results are hypothetical</h2>
              <p>
                Astrix runs on simulated spacecraft, first-order launch and intercept models, and advisory AI reasoning.
                Nothing it produces is flight-qualified. Every result must be verified against real-time prototypes,
                hardware-in-the-loop tests and uploaded flight or test data before it informs an engineering or
                operational decision.
              </p>
              <p className="l-muted">
                AI agents propose; a deterministic safety engine and a digital twin verify; high-risk actions wait for a
                human.
              </p>
            </div>
          </div>
        </section>

        <section className="l-final">
          <h2>
            Start a test <span className="l-hl">campaign</span>
          </h2>
          <p>Create an account and fly your first mission in under a minute.</p>
          <a className="l-btn primary" href="/app">
            Get started <Glyph d={ARROW} size={17} />
          </a>
        </section>
      </main>

      <footer className="l-footer">
        <div className="l-footer-brand">
          <a className="l-brand" href="/">
            <AstrixMark size={24} tone="nebula" />
            <span>Astrix</span>
          </a>
          <p>Agentic AI for spacecraft anomaly assurance, intercept testing and hardware-in-the-loop prototypes.</p>
        </div>
        <div className="l-footer-col">
          <strong>Product</strong>
          <a href="#capabilities">Capabilities</a>
          <a href="#hardware">Hardware</a>
          <a href="#astrix-lm">Astrix-LM</a>
        </div>
        <div className="l-footer-col">
          <strong>Console</strong>
          <a href="/app">Sign in</a>
          <a href="/app">Create account</a>
          <a href="#safety">Safety notice</a>
        </div>
        <div className="l-footer-bottom">
          <span>© {new Date().getFullYear()} Astrix</span>
          <span>Results are hypothetical and must be verified.</span>
        </div>
      </footer>
    </div>
  )
}
