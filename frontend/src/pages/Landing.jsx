// Public landing page for Astrix-AI. Deliberately dependency-free (no charts,
// no WebSocket) so it is the fastest thing on the domain; the console loads as
// a separate chunk from /app.

import '../landing.css'

const LOOP = ['Detect', 'Remember', 'Diagnose', 'Assess', 'Plan', 'Simulate', 'Verify', 'Recover', 'Learn']

const CAPABILITIES = [
  {
    title: 'Flight Assurance',
    body: 'Fly the ascent, stress it with launch faults, then inject on-orbit anomalies. Watch Astrix detect, diagnose, plan and recover, before and after launch.',
    icon: 'M3 12h3l2.5-6 4 12 2.5-6H21',
  },
  {
    title: 'Intercept Lab',
    body: 'Check an interceptor’s trajectory and predicted intercept point, then inject seeker, guidance or cyber faults and see what the ground can detect.',
    icon: 'M12 4a8 8 0 100 16 8 8 0 000-16zM12 9a3 3 0 100 6 3 3 0 000-6zM12 1v4M12 19v4M1 12h4M19 12h4',
  },
  {
    title: 'Vehicle Studio',
    body: 'Describe a rocket and satellite in plain English or edit every stage. Get Δv, thrust-to-weight and power budgets, then fly the design in the 2D launch panel.',
    icon: 'M12 2c3 2.5 4.5 6 4.5 10.5V17h-9v-4.5C7.5 8 9 4.5 12 2zM7.5 14L5 17v3l2.5-1.5M16.5 14l2.5 3v3l-2.5-1.5',
  },
  {
    title: 'Hardware Link',
    body: 'Plug an Arduino prototype into the browser. Stream real sensors, send commands, inject faults on the chip, and let Astrix close the loop on physical actuators.',
    icon: 'M6 6h12v12H6zM9.5 9.5h5v5h-5zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4',
  },
  {
    title: 'Astrix-LM',
    body: 'Every verified decision becomes an encrypted, hash-chained training example. Train Astrix’s own nano model in seconds, or fine-tune a small open LLM.',
    icon: 'M6 4a2 2 0 100 4 2 2 0 000-4zM18 4a2 2 0 100 4 2 2 0 000-4zM6 16a2 2 0 100 4 2 2 0 000-4zM18 16a2 2 0 100 4 2 2 0 000-4zM12 9.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z',
  },
  {
    title: 'Free reasoners',
    body: 'Switch between Gemini, Groq, OpenRouter, Mistral, Cohere, Hugging Face, Cloudflare and local Ollama models at runtime, with deterministic fallback when a model fails.',
    icon: 'M12 3l1.8 4.6L18.5 9l-4.7 1.5L12 15l-1.8-4.5L5.5 9l4.7-1.4L12 3z',
  },
]

const PROVIDERS = ['Gemini', 'Groq', 'OpenRouter', 'Mistral', 'Cohere', 'Hugging Face', 'Cloudflare', 'NVIDIA NIM', 'Ollama', 'Claude', 'OpenAI']

function Glyph({ d }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

function Warning() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.3 3.9L2.4 17.5A2 2 0 004.1 20.5h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4.5M12 17h.01" />
    </svg>
  )
}

function OrbitVisual() {
  return (
    <div className="hero-visual" aria-hidden="true">
      <svg viewBox="0 0 400 400">
        <defs>
          <radialGradient id="earth" cx="40%" cy="35%" r="70%">
            <stop offset="0" stopColor="#2f6fa3" />
            <stop offset="0.7" stopColor="#12324f" />
            <stop offset="1" stopColor="#0b1a2b" />
          </radialGradient>
          <linearGradient id="trail" x1="0" x2="1">
            <stop offset="0" stopColor="#7c93ff" stopOpacity="0" />
            <stop offset="1" stopColor="#7c93ff" stopOpacity="0.9" />
          </linearGradient>
        </defs>
        <circle cx="200" cy="200" r="190" fill="none" stroke="#26262c" strokeDasharray="2 6" />
        <circle cx="200" cy="200" r="140" fill="none" stroke="#2c2c33" />
        <circle cx="200" cy="200" r="92" fill="url(#earth)" />
        <path d="M150 180c14-10 30-6 38 4s24 8 30-2M168 232c10 6 26 6 36-4" stroke="#1d4a36" strokeWidth="10" strokeLinecap="round" fill="none" opacity="0.7" />
        <g className="orbit-spin">
          <path d="M60 200 A140 140 0 0 1 200 60" stroke="url(#trail)" strokeWidth="2" fill="none" />
          <g transform="translate(200 60)">
            <rect x="-16" y="-3" width="11" height="6" fill="#3d6fb8" />
            <rect x="5" y="-3" width="11" height="6" fill="#3d6fb8" />
            <rect x="-5" y="-5" width="10" height="10" rx="1.5" fill="#e8e6dc" />
            <circle r="16" fill="none" stroke="#0ca30c" strokeOpacity="0.5" className="pulse" />
          </g>
        </g>
        <g className="orbit-spin slow">
          <circle cx="390" cy="200" r="3" fill="#fab219" />
        </g>
      </svg>
    </div>
  )
}

export default function Landing() {
  return (
    <div className="landing">
      <header className="l-nav">
        <a className="l-brand" href="/">
          <img src="/astrix-emblem.svg" alt="" width="26" height="26" />
          <span>Astrix-AI</span>
        </a>
        <nav aria-label="Sections">
          <a href="#capabilities">Capabilities</a>
          <a href="#hardware">Hardware</a>
          <a href="#astrix-lm">Astrix-LM</a>
          <a href="#safety">Safety</a>
        </nav>
        <a className="l-btn primary small" href="/app">
          Open console
        </a>
      </header>

      <main>
        <section className="l-hero">
          <div className="l-hero-copy">
            <p className="l-eyebrow">Agentic AI for spacecraft and interceptor testing</p>
            <h1>Find the failure before the flight does.</h1>
            <p className="l-lead">
              Astrix-AI launches, stresses and recovers simulated spacecraft, checks interceptor trajectories, sizes
              custom rockets and satellites, and drives real Arduino prototypes, with an AI loop that detects,
              reasons, verifies and learns.
            </p>
            <div className="l-cta">
              <a className="l-btn primary" href="/app">
                Open the console
              </a>
              <a className="l-btn" href="#capabilities">
                See what it does
              </a>
            </div>
            <p className="l-hero-note">
              <Warning />
              Results are hypothetical and must be verified with real-time prototypes and uploaded data.
            </p>
          </div>
          <OrbitVisual />
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
            <h2>One workspace, every stage of the test campaign</h2>
            <p>Operate it with buttons or in plain language. The assistant runs the same verified actions as the buttons.</p>
          </div>
          <div className="l-grid">
            {CAPABILITIES.map((c) => (
              <article key={c.title} className="l-card">
                <div className="l-card-icon">
                  <Glyph d={c.icon} />
                </div>
                <h3>{c.title}</h3>
                <p>{c.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="l-section l-split" id="hardware">
          <div>
            <p className="l-eyebrow">Hardware in the loop</p>
            <h2>From the simulator to a real prototype on your bench</h2>
            <p>
              Flash the Astrix HIL firmware onto an Arduino Uno, Nano, Mega, ESP32 or RP2040. The console talks to it
              over Web Serial straight from the browser, so it works when the app is hosted on Vercel. Calibrated
              readings perturb the orbiting spacecraft; Astrix’s approved recovery actions switch real motors and loads.
            </p>
            <ul className="l-list">
              <li>Thermistor, bus voltage, current, light, IMU vibration and gyro, wheel RPM</li>
              <li>Eight on-chip fault injections that ramp like real failures</li>
              <li>Serial bridge for headless benches and CI</li>
            </ul>
          </div>
          <pre className="l-code" aria-label="Serial protocol example">
            <code>
              <span className="c-dim">{'// board → console, 5 Hz'}</span>
              {'\n{"temp_c":24.6,"bus_v":5.01,"vib_g":0.08,\n "rpm":2950,"fault":"NONE","mode":"NOMINAL"}\n\n'}
              <span className="c-dim">{'// console → board'}</span>
              {'\nINJECT VIB_SPIKE 0.8\nACT ISOLATE_WHEEL\n\n'}
              <span className="c-dim">{'// board → console'}</span>
              {'\n{"ack":"ACT ISOLATE_WHEEL","ok":true}'}
            </code>
          </pre>
        </section>

        <section className="l-section l-split reverse" id="astrix-lm">
          <div className="l-stats">
            <div>
              <strong>Fernet</strong>
              <span>encrypted at rest</span>
            </div>
            <div>
              <strong>SHA-256</strong>
              <span>hash-chained corpus</span>
            </div>
            <div>
              <strong>Shadow</strong>
              <span>agreement scoring</span>
            </div>
            <div>
              <strong>LoRA</strong>
              <span>small-LLM fine-tuning</span>
            </div>
          </div>
          <div>
            <p className="l-eyebrow">Astrix-LM</p>
            <h2>A model that learns from verified decisions</h2>
            <p>
              Deterministic reasoners and a safety engine produce decisions you can audit. Astrix captures them securely
              and keeps training its own small model, scoring it in shadow against live decisions so you can see when it
              has earned trust, and export the corpus to fine-tune an open LLM served locally.
            </p>
          </div>
        </section>

        <section className="l-section" aria-label="Reasoners">
          <div className="l-section-head">
            <h2>Bring any free model or none at all</h2>
            <p>
              Every agent has a deterministic fallback. Add a free API key and switch reasoners at runtime from the
              console.
            </p>
          </div>
          <div className="l-providers">
            {PROVIDERS.map((p) => (
              <span key={p}>{p}</span>
            ))}
          </div>
        </section>

        <section className="l-section" id="safety">
          <div className="l-safety">
            <div className="l-safety-icon">
              <Warning />
            </div>
            <div>
              <h2>Results are hypothetical</h2>
              <p>
                Astrix-AI runs on simulated spacecraft, first-order launch and intercept models, and advisory AI
                reasoning. Nothing it produces is flight-qualified. Every result must be verified against real-time
                prototypes, hardware-in-the-loop tests and uploaded flight or test data before it informs an engineering
                or operational decision.
              </p>
              <p className="l-muted">
                AI agents propose; a deterministic safety engine and a digital twin verify; high-risk actions wait for a
                human.
              </p>
            </div>
          </div>
        </section>

        <section className="l-final">
          <h2>Start a test campaign</h2>
          <a className="l-btn primary" href="/app">
            Open the console
          </a>
        </section>
      </main>

      <footer className="l-footer">
        <span>© {new Date().getFullYear()} Astrix-AI</span>
        <span>Detect · Reason · Recover · Learn</span>
      </footer>
    </div>
  )
}
