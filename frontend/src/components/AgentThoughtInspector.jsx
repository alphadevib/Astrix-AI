import { useState } from 'react'
import { INK, STATUS } from '../theme'
import { Panel, Pill } from './primitives'

export default function AgentThoughtInspector({ criticReview, thoughts }) {
  const [expanded, setExpanded] = useState(true)

  const latestThought = thoughts?.[0]

  return (
    <Panel
      title="Agentic AI Neural Inspector & Critic Debate"
      note="Devil's Advocate Analysis & Ontological Knowledge Graph"
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {criticReview && (
            <>
              <Pill
                label={`Confidence: ${(criticReview.confidence_score * 100).toFixed(0)}%`}
                color={criticReview.confidence_score > 0.8 ? STATUS.nominal : STATUS.warning}
              />
              <Pill
                label={`Sensor Spoofing Risk: ${criticReview.sensor_spoofing_risk}`}
                color={criticReview.sensor_spoofing_risk === 'HIGH' ? STATUS.critical : STATUS.nominal}
              />
              {criticReview.confirmation_bias_detected && (
                <Pill label="BIAS ALERT DETECTED" color={STATUS.critical} />
              )}
            </>
          )}
        </div>
        <button
          className="btn"
          style={{ fontSize: 11, padding: '2px 8px' }}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? 'Collapse Drawer' : 'Expand Reasoning Inspector'}
        </button>
      </div>

      {expanded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Critic Consensus */}
          {criticReview && (
            <div style={{ background: 'rgba(255,255,255,0.03)', padding: 10, borderRadius: 6 }}>
              <div className="small muted" style={{ marginBottom: 4 }}>Critic Consensus Recommendation:</div>
              <p className="small" style={{ color: INK.primary, margin: 0, lineHeight: 1.5 }}>
                {criticReview.consensus_recommendation}
              </p>
              {criticReview.counter_arguments?.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <span className="small" style={{ color: STATUS.warning, fontWeight: 600 }}>
                    Counter-Evidence & Scrutiny:
                  </span>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 11, color: INK.secondary }}>
                    {criticReview.counter_arguments.map((arg, idx) => (
                      <li key={idx}>{arg}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Knowledge Graph Ontology Explanation */}
          {latestThought?.knowledge_graph_explanation && (
            <div style={{ background: 'rgba(70, 130, 180, 0.08)', borderLeft: `3px solid ${STATUS.info}`, padding: '8px 12px' }}>
              <div className="small muted">Spacecraft Ontological Knowledge Graph:</div>
              <p className="small" style={{ color: INK.primary, margin: '2px 0 0', lineHeight: 1.4 }}>
                {latestThought.knowledge_graph_explanation}
              </p>
            </div>
          )}

          {/* Ruled-Out Hypotheses Distribution */}
          {criticReview?.ruled_out_hypotheses?.length > 0 && (
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>
                Adversarial Hypotheses Scrutinized & Ruled Out:
              </div>
              <div className="grid cols-2" style={{ gap: 8 }}>
                {criticReview.ruled_out_hypotheses.map((item, idx) => (
                  <div
                    key={idx}
                    style={{
                      background: 'rgba(0,0,0,0.25)',
                      padding: 8,
                      borderRadius: 4,
                      borderLeft: `2px solid ${item.verdict === 'RULED_OUT' ? STATUS.nominal : STATUS.warning}`,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <strong style={{ fontSize: 11, color: INK.primary }}>{item.hypothesis}</strong>
                      <span className="small muted">P = {(item.plausibility * 100).toFixed(0)}%</span>
                    </div>
                    <div style={{ fontSize: 10, color: INK.muted, marginTop: 3 }}>
                      {item.reasoning}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Live Thought Stream */}
          {thoughts?.length > 0 && (
            <div>
              <div className="small muted" style={{ marginBottom: 4 }}>Agent Reasoning Stream:</div>
              <div style={{ maxHeight: 120, overflowY: 'auto', background: 'rgba(0,0,0,0.3)', padding: 8, borderRadius: 4 }}>
                {thoughts.map((th, idx) => (
                  <div key={idx} style={{ fontSize: 11, marginBottom: 4, color: INK.secondary, borderBottom: `1px solid rgba(255,255,255,0.05)`, paddingBottom: 3 }}>
                    <span style={{ color: STATUS.info }}>[{th.stage}]</span> {th.diagnosis ? `Hypothesis: ${th.diagnosis}` : ''}{' '}
                    {th.consensus || ''}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}
