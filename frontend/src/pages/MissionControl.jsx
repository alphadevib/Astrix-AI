// The mission-control screen.
//
// Reading order top to bottom is the loop's own order: what the spacecraft is
// doing, then what ASTRIX detected, then what it concluded, then what it
// proposes, then what verification said, then what it learned. An operator
// scanning downward is walking the decision chain.

import { useState } from 'react'
import api from '../services/api'
import { STATUS } from '../theme'
import { AgentActivity, LearningPanel } from '../components/AgentActivity'
import { DetectionPanel, DiagnosisPanel, MemoryPanel, RiskPanel } from '../components/AnalysisPanels'
import LoopTrail from '../components/LoopTrail'
import { MissionControls, Vitals } from '../components/MissionBar'
import { ApprovalBanner, PlanPanel, SafetyPanel, SimulationPanel } from '../components/RecoveryPanels'
import { Panel } from '../components/primitives'
import MissionTheater from '../components/MissionTheater'
import TelemetryCharts from '../components/TelemetryCharts'

export default function MissionControl({ stream }) {
  const { frames, latest, cycle, activity, approval, learning, outcome, mission } = stream
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const decide = async (anomalyId, actionId, approved) => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.approve(anomalyId, actionId, approved)
      // Clear optimistically: the websocket `approval` event will confirm, but the
      // operator should not see a live button after they have clicked it.
      stream.setApproval(null)
      // An approval can be refused after the fact — the anomaly closed, or the
      // spacecraft state changed and the action no longer passes verification.
      if (approved && result.approval !== 'APPROVED') setError(result.message)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid" style={{ gap: 14 }}>
      {error && (
        <div className="banner" style={{ borderColor: STATUS.critical }}>
          <div className="banner-text">{error}</div>
        </div>
      )}

      <ApprovalBanner approval={approval} onDecide={decide} busy={busy} />

      <MissionControls mission={mission} onError={setError} />

      <MissionTheater stream={stream} />

      {latest && <Vitals frame={latest} resources={cycle?.resources} />}

      <Panel title="ASTRIX loop" note="Detect · Diagnose · Remember · Plan · Simulate · Verify · Recover · Learn">
        <LoopTrail cycle={cycle} learning={learning} />
      </Panel>

      <div className="grid main-split">
        <TelemetryCharts frames={frames} />
        <AgentActivity activity={activity} />
      </div>

      <div className="grid cols-2">
        <DetectionPanel detection={cycle?.detection} />
        <MemoryPanel recall={cycle?.recall} />
      </div>

      <div className="grid cols-2">
        <DiagnosisPanel diagnosis={cycle?.diagnosis} />
        <RiskPanel risk={cycle?.risk} resources={cycle?.resources} />
      </div>

      <PlanPanel plan={cycle?.plan} selectedId={cycle?.safety?.action_id} />

      <div className="grid cols-2">
        <SimulationPanel simulation={cycle?.simulation} />
        <SafetyPanel safety={cycle?.safety} />
      </div>

      <LearningPanel learning={learning} outcome={outcome} />
    </div>
  )
}
