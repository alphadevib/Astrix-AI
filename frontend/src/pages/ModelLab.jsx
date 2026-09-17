// Astrix-LM — the secure training corpus and Astrix's own models.
//
// Every verified decision the agents make becomes an encrypted, hash-chained
// training example. The nano model trains on them in seconds and is scored in
// shadow against live decisions; the corpus exports as chat-format JSONL for
// fine-tuning a small open LLM that serves back through the Local reasoner.

import { useEffect, useState } from 'react'
import api from '../services/api'
import { InlineNotice } from '../components/Disclaimer'
import { Empty, Panel, Pill, fmt } from '../components/primitives'
import { INK, STATUS } from '../theme'

export default function ModelLab() {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(null)
  const [message, setMessage] = useState(null)
  const [verify, setVerify] = useState(null)

  const load = () =>
    api
      .modelStatus()
      .then(setStatus)
      .catch((err) => setMessage({ tone: 'error', text: err.message }))

  useEffect(() => {
    load()
    const id = window.setInterval(load, 5000)
    return () => window.clearInterval(id)
  }, [])

  const act = async (name, fn) => {
    setBusy(name)
    setMessage(null)
    try {
      await fn()
      await load()
    } catch (err) {
      setMessage({ tone: 'error', text: err.message })
    } finally {
      setBusy(null)
    }
  }

  const train = (onlyVerified) =>
    act('train', async () => {
      const result = await api.trainModel(onlyVerified)
      setMessage(
        result.trained
          ? { tone: 'good', text: `Trained ${result.version} on ${result.examples} examples.` }
          : { tone: 'warn', text: result.reason },
      )
    })

  const exportJsonl = () =>
    act('export', async () => {
      const text = await api.exportCorpus(true)
      const blob = new Blob([text], { type: 'application/x-ndjson' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'astrix-lm-sft.jsonl'
      a.click()
      URL.revokeObjectURL(url)
      setMessage({ tone: 'good', text: `Exported ${text ? text.split('\n').length : 0} verified examples.` })
    })

  if (!status) {
    return <Empty>{message?.text ?? 'Loading Astrix-LM…'}</Empty>
  }

  const { corpus, nano } = status
  const latest = nano.runs?.at(-1)
  const agreement = nano.shadow?.agreement

  return (
    <div className="grid" style={{ gap: 'var(--gap)' }}>
      {message && (
        <div className={`banner tone-${message.tone}`} role="status">
          <div className="banner-text">{message.text}</div>
        </div>
      )}

      <div className="tile-grid">
        <Stat label="Training examples" value={corpus.examples} sub={`auto-train every ${status.auto_train_every || '—'}`} />
        <Stat label="Deterministic-labelled" value={corpus.by_reasoner?.DETERMINISTIC ?? 0} sub={`LLM-labelled ${corpus.by_reasoner?.LLM ?? 0}`} />
        <Stat label="Safety-verified" value={corpus.by_safety_status?.PASS ?? 0} sub="passed the safety engine" />
        <Stat label="Current model" value={nano.version ?? 'none'} sub={latest ? fmt.time(latest.trained_at) : 'train once examples exist'} />
        <Stat
          label="Shadow agreement"
          value={agreement == null ? '—' : fmt.pct(agreement)}
          sub={`${nano.shadow?.compared ?? 0} live decisions compared`}
          color={agreement == null ? undefined : agreement > 0.9 ? STATUS.good : STATUS.warning}
        />
      </div>

      <div className="grid cols-2">
        <Panel title="Train Astrix-LM nano" note="scikit-learn · CPU · seconds">
          <p className="small muted" style={{ marginTop: 0, lineHeight: 1.6 }}>
            Learns subsystem, failure mode and recovery action from the detection context the agents saw. It never
            executes anything: its shadow agreement with the verified reasoners is the evidence to review before giving
            a learned model any authority.
          </p>
          <div className="control-row">
            <button type="button" className="btn primary" disabled={busy !== null} onClick={() => train(false)}>
              {busy === 'train' ? 'Training…' : 'Train on all examples'}
            </button>
            <button type="button" className="btn" disabled={busy !== null} onClick={() => train(true)}>
              Safety-verified only
            </button>
          </div>
          {latest ? (
            <table className="data" style={{ marginTop: 14 }}>
              <thead>
                <tr>
                  <th>Head</th>
                  <th>Classes</th>
                  <th>Held-out acc.</th>
                  <th>Train acc.</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(latest.metrics).map(([head, m]) => (
                  <tr key={head}>
                    <td>{fmt.title(head)}</td>
                    <td className="mono">{m.classes}</td>
                    <td className="mono" title={m.evaluation}>
                      {m.accuracy == null ? 'n/a' : fmt.pct(m.accuracy, 1)}
                    </td>
                    <td className="mono">{m.train_accuracy == null ? '—' : fmt.pct(m.train_accuracy, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>
              No model yet. Run missions and inject faults in Flight Assurance; every diagnosed cycle adds an example.
            </Empty>
          )}
        </Panel>

        <Panel title="Secure corpus" note={corpus.encryption}>
          <ul className="check-list">
            <li>Payloads encrypted at rest; key from {corpus.key_source}.</li>
            <li>SHA-256 hash chain over every example detects deletion, reordering or edits.</li>
            <li>Duplicates skipped; only filter labels stored in clear.</li>
            <li>Each model version records the chain head it was trained on.</li>
          </ul>
          <p className="mono small muted" style={{ overflowWrap: 'anywhere' }}>
            chain head {corpus.chain_head.slice(0, 24)}…
          </p>
          <div className="control-row">
            <button
              type="button"
              className="btn"
              disabled={busy !== null}
              onClick={() => act('verify', async () => setVerify(await api.verifyCorpus()))}
            >
              {busy === 'verify' ? 'Verifying…' : 'Verify integrity'}
            </button>
            <button type="button" className="btn" disabled={busy !== null || !corpus.examples} onClick={exportJsonl}>
              Export JSONL for fine-tuning
            </button>
            {verify && (
              <Pill color={verify.ok ? STATUS.good : STATUS.critical}>
                {verify.ok ? `intact · ${verify.checked} checked` : `broken at #${verify.broken_at}: ${verify.reason}`}
              </Pill>
            )}
          </div>
        </Panel>
      </div>

      <Panel title="From nano to a generative Astrix-LM" note="scripts/train_astrix_lm.py">
        <ol className="steps">
          <li>
            Export the verified corpus (button above, or <code>POST /model/export</code>).
          </li>
          <li>
            Fine-tune a small open model with LoRA: <code>python scripts/train_astrix_lm.py --data astrix-lm-sft.jsonl</code>{' '}
            (default base <code>Qwen/Qwen2.5-0.5B-Instruct</code>; GPU recommended).
          </li>
          <li>
            Convert to GGUF and register with Ollama: <code>ollama create astrix-lm -f training/Modelfile</code>.
          </li>
          <li>
            Pick <strong>Local (Ollama) → astrix-lm</strong> in the reasoner menu. Safety verification still gates every
            action.
          </li>
        </ol>
        {latest && (
          <div className="runs">
            {nano.runs
              .slice()
              .reverse()
              .map((run) => (
                <div key={run.version} className="run">
                  <strong>{run.version}</strong>
                  <span>{run.examples} examples</span>
                  <span className="mono muted">{run.sha256.slice(0, 12)}</span>
                  <span className="muted">{new Date(run.trained_at).toLocaleString()}</span>
                </div>
              ))}
          </div>
        )}
        <InlineNotice>Learned models inherit the limits of simulated training data; validate them on prototypes.</InlineNotice>
      </Panel>
    </div>
  )
}

function Stat({ label, value, sub, color }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value" style={{ fontSize: 20, color: color ?? INK.primary }}>
        {value}
      </div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}
