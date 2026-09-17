// The reasoning chain: detection breakdown, mission memory, diagnosis, risk.
//
// Every panel here exists to make a decision inspectable. The score breakdown in
// particular is the answer to "how do you reduce false alarms" — it shows which
// of the six terms moved the score, so a suppressed alert can be defended rather
// than merely asserted.

import { INK, SERIES, STATUS } from '../theme'
import { Empty, KeyValue, LevelPill, OutcomePill, Panel, Pill, SeverityPill, fmt } from './primitives'

export function DetectionPanel({ detection }) {
  if (!detection) {
    return (
      <Panel title="Detection">
        <Empty>Waiting for the first scored frame.</Empty>
      </Panel>
    )
  }

  const factors = detection.context_factors ?? []
  const maxContribution = Math.max(...factors.map((f) => Math.abs(f.contribution)), 0.001)

  return (
    <Panel
      title="Detection"
      note={`${detection.detector} · window ${detection.window_size}`}
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <SeverityPill severity={detection.severity} suppressed={detection.suppressed} />
        <Pill>ml {fmt.num(detection.ml_score, 3)}</Pill>
        <Pill color={SERIES[0]}>risk {fmt.num(detection.final_score, 3)}</Pill>
        <Pill>elevated {fmt.num(detection.persistence_seconds, 0)}s</Pill>
      </div>

      {/* One bar per scoring term: magnitude, single series, direct-labelled. */}
      <div style={{ display: 'grid', gap: 7 }}>
        {factors.map((factor) => (
          <div key={factor.name}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontFamily: 'var(--mono)',
                fontSize: 10.5,
                color: INK.secondary,
              }}
            >
              <span>{fmt.title(factor.name)}</span>
              <span>
                {fmt.num(factor.value, 2)} × {fmt.num(factor.weight, 2)} ={' '}
                <strong style={{ color: INK.primary }}>{fmt.num(factor.contribution, 3)}</strong>
              </span>
            </div>
            <div
              style={{
                height: 6,
                background: 'var(--surface-2)',
                borderRadius: 3,
                overflow: 'hidden',
                margin: '3px 0 2px',
              }}
            >
              <div
                style={{
                  width: `${(Math.abs(factor.contribution) / maxContribution) * 100}%`,
                  height: '100%',
                  background: SERIES[0],
                  borderRadius: 3,
                }}
              />
            </div>
            <div className="small muted" style={{ lineHeight: 1.45 }}>
              {factor.note}
            </div>
          </div>
        ))}
      </div>

      {detection.suppressed && (
        <div
          className="evidence"
          style={{ marginTop: 12, borderLeftColor: STATUS.warning }}
        >
          <strong className="mono" style={{ fontSize: 10.5, color: STATUS.warning }}>
            FALSE ALARM SUPPRESSED
          </strong>
          <div style={{ marginTop: 4 }}>{detection.suppression_reason}</div>
        </div>
      )}

      {detection.deviating_parameters?.length > 0 && (
        <p className="small muted" style={{ marginTop: 11, marginBottom: 0, lineHeight: 1.5 }}>
          <span className="mono">CHANNELS BEYOND 3 SIGMA:</span>{' '}
          {detection.deviating_parameters.join(', ')}
        </p>
      )}
    </Panel>
  )
}

export function MemoryPanel({ recall }) {
  if (!recall || (!recall.hits?.length && !recall.lessons?.length)) {
    return (
      <Panel title="Mission memory">
        <Empty>No comparable historical record retrieved for the current frame.</Empty>
      </Panel>
    )
  }

  return (
    <Panel title="Mission memory" note={`${recall.hits?.length ?? 0} records retrieved`}>
      {recall.hits?.map((hit) => (
        <div key={hit.title + hit.similarity} style={{ marginBottom: 11 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="mono small" style={{ color: SERIES[0] }}>
              {fmt.num(hit.similarity, 2)}
            </span>
            <strong style={{ fontSize: 13 }}>{hit.title}</strong>
            <OutcomePill outcome={hit.outcome} />
          </div>
          <div className="small muted" style={{ marginTop: 2 }}>
            {hit.mission_id} · {hit.source}
            {hit.recovery_action ? ` · recovery: ${fmt.title(hit.recovery_action)}` : ''}
          </div>
          <div className="evidence" style={{ marginTop: 5 }}>
            {hit.excerpt}
          </div>
        </div>
      ))}

      {recall.action_success_rates && Object.keys(recall.action_success_rates).length > 0 && (
        <>
          <div className="divider" />
          <h3 className="panel-title" style={{ marginBottom: 7 }}>
            Measured recovery effectiveness
          </h3>
          <table className="data">
            <tbody>
              {Object.entries(recall.action_success_rates)
                .sort((a, b) => b[1] - a[1])
                .map(([actionId, rate]) => (
                  <tr key={actionId}>
                    <td>{fmt.title(actionId)}</td>
                    <td className="num" style={{ color: rate >= 0.7 ? STATUS.good : INK.secondary }}>
                      {fmt.num(rate, 2)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 7, marginBottom: 0 }}>
            From recorded outcomes in the structured store — measured, not generated.
          </p>
        </>
      )}

      {recall.lessons?.length > 0 && (
        <>
          <div className="divider" />
          <h3 className="panel-title" style={{ marginBottom: 7 }}>
            Lessons on record
          </h3>
          <ul className="clean">
            {recall.lessons.map((lesson) => (
              <li key={lesson}>{lesson}</li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  )
}

export function DiagnosisPanel({ diagnosis }) {
  if (!diagnosis) {
    return (
      <Panel title="Diagnosis">
        <Empty>No anomaly under diagnosis.</Empty>
      </Panel>
    )
  }

  return (
    <Panel title="Diagnosis" note={`reasoner: ${diagnosis.reasoner}`}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <Pill color={SERIES[0]}>{diagnosis.subsystem}</Pill>
        {diagnosis.component && <Pill>{fmt.title(diagnosis.component)}</Pill>}
        <Pill color={diagnosis.confidence >= 0.8 ? STATUS.good : STATUS.warning}>
          confidence {fmt.pct(diagnosis.confidence)}
        </Pill>
        {diagnosis.historical_match && <Pill color={SERIES[2]}>historical match</Pill>}
      </div>

      <p style={{ margin: '0 0 10px', lineHeight: 1.6, color: INK.secondary }}>
        {diagnosis.probable_cause}
      </p>

      {diagnosis.evidence?.length > 0 && (
        <>
          <h3 className="panel-title" style={{ marginBottom: 5 }}>
            Evidence
          </h3>
          <ul className="clean">
            {diagnosis.evidence.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}

      {diagnosis.ruled_out?.length > 0 && (
        <>
          <h3 className="panel-title" style={{ margin: '11px 0 5px' }}>
            Ruled out
          </h3>
          <ul className="clean muted">
            {diagnosis.ruled_out.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  )
}

export function RiskPanel({ risk, resources }) {
  if (!risk) {
    return (
      <Panel title="Risk & resources">
        <Empty>No risk assessment for the current cycle.</Empty>
      </Panel>
    )
  }

  return (
    <Panel title="Risk & resources" note={`reasoner: ${risk.reasoner}`}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 11 }}>
        <Pill color={INK.secondary}>mission impact</Pill>
        <LevelPill level={risk.mission_impact} />
        {risk.time_to_impact_minutes != null && (
          <Pill color={STATUS.warning}>
            ~{fmt.num(risk.time_to_impact_minutes, 0)} min to impact
          </Pill>
        )}
      </div>

      <table className="data" style={{ marginBottom: 11 }}>
        <tbody>
          {[
            ['Power', risk.power_risk],
            ['Attitude', risk.attitude_risk],
            ['Thermal', risk.thermal_risk],
            ['Communication', risk.communication_risk],
          ].map(([label, level]) => (
            <tr key={label}>
              <td>{label}</td>
              <td style={{ textAlign: 'right' }}>
                <LevelPill level={level} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {risk.cascading_risks?.length > 0 && (
        <>
          <h3 className="panel-title" style={{ marginBottom: 5 }}>
            Cascading risks
          </h3>
          <ul className="clean">
            {risk.cascading_risks.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}

      {risk.rationale && (
        <p className="small muted" style={{ marginTop: 10, marginBottom: 0, lineHeight: 1.55 }}>
          {risk.rationale}
        </p>
      )}

      {resources && (
        <>
          <div className="divider" />
          <KeyValue
            items={[
              ['Wheels', `${resources.operational_wheels?.join(', ') || '—'} operational`],
              ['3-axis control', resources.attitude_control_available ? 'available' : 'LOST'],
              ['Power margin', `${fmt.num(resources.power_margin_w, 1)} W`],
              ['Thermal headroom', `${fmt.num(resources.thermal_headroom_c, 1)} °C`],
              ['Ground link', resources.link_available ? 'healthy' : 'DEGRADED'],
            ]}
          />
        </>
      )}
    </Panel>
  )
}
