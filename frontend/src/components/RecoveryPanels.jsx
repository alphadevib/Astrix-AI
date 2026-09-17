// Recovery planning, digital twin verification and the deterministic safety engine.
//
// The safety panel deliberately lists every rule, not only the violated ones. A
// verification result that shows only failures looks like a filter; showing all
// eleven rules each time makes it visible that the same deterministic checks run
// on every action, which is the entire claim being made.

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { INK, SERIES, STATUS, axisProps, tooltipStyle } from '../theme'
import { Empty, Panel, Pill, RiskPill, VerdictPill, fmt } from './primitives'

export function PlanPanel({ plan, selectedId }) {
  if (!plan?.options?.length) {
    return (
      <Panel title="Recovery plan">
        <Empty>No recovery planned. Planning begins at WARNING severity.</Empty>
      </Panel>
    )
  }

  return (
    <Panel title="Recovery plan" note={`reasoner: ${plan.reasoner}`}>
      <table className="data">
        <thead>
          <tr>
            <th style={{ width: 22 }} />
            <th>Action</th>
            <th>Risk</th>
            <th style={{ textAlign: 'right' }}>Recorded</th>
            <th style={{ textAlign: 'right' }}>Score</th>
          </tr>
        </thead>
        <tbody>
          {plan.options.map((option) => {
            const chosen = option.action_id === (selectedId ?? plan.selected_action_id)
            const blocked = option.parameters?.blocking_concern
            return (
              <tr key={option.action_id} className={chosen ? 'selected' : ''}>
                <td className="mono" style={{ color: SERIES[0] }}>
                  {chosen ? '▸' : ''}
                </td>
                <td>
                  <div style={{ color: chosen ? INK.primary : INK.secondary }}>
                    {fmt.title(option.action_id)}
                  </div>
                  <div className="small muted" style={{ marginTop: 2, lineHeight: 1.45 }}>
                    {option.rationale}
                  </div>
                  {blocked && (
                    <div
                      className="small"
                      style={{ marginTop: 3, color: STATUS.warning, lineHeight: 1.45 }}
                    >
                      ⚠ {blocked}
                    </div>
                  )}
                </td>
                <td>
                  <RiskPill risk={option.risk_level} />
                </td>
                <td className="num">
                  {option.historical_success_rate == null
                    ? '—'
                    : fmt.num(option.historical_success_rate, 2)}
                </td>
                <td className="num">{fmt.num(option.score, 3)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="small muted" style={{ marginTop: 9, marginBottom: 0, lineHeight: 1.5 }}>
        Risk tiers come from the verified action catalogue and recorded effectiveness comes from
        past outcomes — neither is authored by the reasoning agent. The agent contributes the
        ordering and the justification.
      </p>
    </Panel>
  )
}

export function SimulationPanel({ simulation }) {
  if (!simulation) {
    return (
      <Panel title="Digital twin">
        <Empty>No simulation run for the current cycle.</Empty>
      </Panel>
    )
  }

  const trajectory = simulation.trajectory ?? {}
  const t = trajectory.t ?? []
  const data = t.map((time, index) => ({
    t: time,
    action: trajectory.attitude_error_deg?.[index],
    baseline: trajectory.baseline_attitude_error_deg?.[index],
  }))

  return (
    <Panel
      title="Digital twin"
      note={`${simulation.horizon_seconds}s horizon · ${fmt.title(simulation.action_id)}`}
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 11 }}>
        <VerdictPill status={simulation.status} />
        <Pill color={SERIES[0]}>
          predicted benefit {fmt.pct(simulation.effectiveness_estimate)}
        </Pill>
      </div>

      {data.length > 0 && (
        <ResponsiveContainer width="100%" height={158}>
          <LineChart data={data} margin={{ top: 4, right: 10, bottom: 0, left: -20 }}>
            <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="t" {...axisProps} unit="s" minTickGap={40} />
            <YAxis {...axisProps} width={46} />
            <Tooltip {...tooltipStyle} />
            <Legend
              verticalAlign="top"
              height={22}
              iconType="plainline"
              iconSize={14}
              wrapperStyle={{ fontSize: 11, color: INK.secondary }}
            />
            <ReferenceLine
              y={0.5}
              stroke={STATUS.warning}
              strokeDasharray="4 4"
              strokeWidth={1}
              label={{
                value: 'pointing budget',
                position: 'insideTopRight',
                fill: STATUS.warning,
                fontSize: 10,
              }}
            />
            <Line
              type="monotone"
              dataKey="action"
              name="With this action"
              stroke={SERIES[0]}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            {/* The baseline is a reference, not a category — muted and dashed so it
                never competes with the series under evaluation. */}
            <Line
              type="monotone"
              dataKey="baseline"
              name="If nothing is done"
              stroke={INK.muted}
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}

      <table className="data" style={{ marginTop: 8 }}>
        <tbody>
          {simulation.checks?.map((check) => (
            <tr key={check.name}>
              <td style={{ width: 62 }}>
                <Pill color={check.passed ? STATUS.good : STATUS.critical}>
                  {check.passed ? 'pass' : 'fail'}
                </Pill>
              </td>
              <td>
                <div style={{ color: INK.primary }}>{fmt.title(check.name)}</div>
                <div className="small muted">{check.detail}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="small muted" style={{ marginTop: 9, marginBottom: 0, lineHeight: 1.5 }}>
        The twin gates on safety, not on effectiveness: an action passes if it keeps the spacecraft
        inside vehicle limits and no worse off than inaction. Whether it fixes the fault is reported
        separately as predicted benefit.
      </p>
    </Panel>
  )
}

export function SafetyPanel({ safety }) {
  if (!safety) {
    return (
      <Panel title="Safety verification">
        <Empty>No action has been put to the constraint engine this cycle.</Empty>
      </Panel>
    )
  }

  const violations = safety.rules?.filter((rule) => rule.violated) ?? []

  return (
    <Panel title="Safety verification" note={`audit ${safety.audit_id}`}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 11 }}>
        <VerdictPill status={safety.status} />
        <RiskPill risk={safety.effective_risk_level} />
        <Pill
          color={
            safety.approval === 'AUTO_APPROVED'
              ? STATUS.good
              : safety.approval === 'BLOCKED'
                ? STATUS.critical
                : STATUS.warning
          }
        >
          {fmt.title(safety.approval)}
        </Pill>
      </div>

      {violations.length > 0 && (
        <div
          className="evidence"
          style={{ borderLeftColor: STATUS.critical, marginBottom: 11 }}
        >
          <strong className="mono" style={{ fontSize: 10.5, color: STATUS.critical }}>
            BLOCKED — THIS CANNOT BE OVERRIDDEN
          </strong>
          <ul className="clean" style={{ marginTop: 5 }}>
            {violations.map((rule) => (
              <li key={rule.rule_id}>
                <span className="mono">{rule.rule_id}</span> — {rule.detail || rule.description}
              </li>
            ))}
          </ul>
        </div>
      )}

      <table className="data">
        <tbody>
          {safety.rules?.map((rule) => (
            <tr key={rule.rule_id}>
              <td className="mono" style={{ width: 56, color: rule.violated ? STATUS.critical : INK.muted }}>
                {rule.rule_id}
              </td>
              <td style={{ color: rule.violated ? STATUS.critical : INK.secondary }}>
                {rule.description}
                {rule.detail && <div className="small muted">{rule.detail}</div>}
              </td>
              <td style={{ width: 44, textAlign: 'right' }} className="mono small">
                {rule.violated ? '✕' : '✓'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

export function ApprovalBanner({ approval, onDecide, busy }) {
  if (!approval) return null
  return (
    <div
      className="banner"
      style={{ borderColor: STATUS.warning, background: 'rgba(250,178,25,0.08)' }}
    >
      <div className="banner-text">
        <div className="banner-title" style={{ color: STATUS.warning }}>
          Human approval required · {approval.risk_level}
        </div>
        <div style={{ marginTop: 5, fontSize: 14 }}>
          {approval.description ?? fmt.title(approval.action_id)}
        </div>
        <div className="small muted" style={{ marginTop: 4 }}>
          Anomaly {approval.anomaly_id} · action <span className="mono">{approval.action_id}</span>
          {approval.simulation_status ? ` · simulation ${approval.simulation_status}` : ''}
          {approval.rationale ? ` — ${approval.rationale}` : ''}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          className="btn approve"
          disabled={busy}
          onClick={() => onDecide(approval.anomaly_id, approval.action_id, true)}
        >
          Approve
        </button>
        <button
          className="btn reject"
          disabled={busy}
          onClick={() => onDecide(approval.anomaly_id, approval.action_id, false)}
        >
          Reject
        </button>
      </div>
    </div>
  )
}
