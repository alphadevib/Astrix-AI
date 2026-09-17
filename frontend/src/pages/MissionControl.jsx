// The mission-control screen.
//
// Reading order top to bottom is the loop's own order: what the spacecraft is
// doing, then what ASTRIX detected, then what it concluded, then what it
// proposes, then what verification said, then what it learned. An operator
// scanning downward is walking the decision chain.
//
// The page is split into named sections with a sticky section nav, so an
// operator can jump straight to "Recovery" when an approval arrives instead of
// scrolling the whole chain. On wide screens the agent activity feed lives in
// a sticky right rail and stays visible at every scroll position.

import { useEffect, useRef, useState } from 'react'
import api from '../services/api'
import { SEVERITY_COLOR, STATUS } from '../theme'
import { AgentActivity, LearningPanel } from '../components/AgentActivity'
import { DetectionPanel, DiagnosisPanel, MemoryPanel, RiskPanel } from '../components/AnalysisPanels'
import LoopTrail from '../components/LoopTrail'
import { MissionControls, Vitals } from '../components/MissionBar'
import { ApprovalBanner, PlanPanel, SafetyPanel, SimulationPanel } from '../components/RecoveryPanels'
import { Panel } from '../components/primitives'
import MissionTheater from '../components/MissionTheater'
import TelemetryCharts from '../components/TelemetryCharts'
import WhatIfSandbox from '../components/WhatIfSandbox'
import AgentThoughtInspector from '../components/AgentThoughtInspector'

const SECTIONS = [
  { key: 'overview', label: 'Overview', description: 'Mission state, the live view and the ASTRIX loop' },
  { key: 'telemetry', label: 'Telemetry', description: 'Subsystem channels against their limits' },
  { key: 'analysis', label: 'Analysis', description: 'Detection, memory recall, diagnosis and risk' },
  { key: 'recovery', label: 'Recovery', description: 'Candidate actions, twin simulation and safety verification' },
  { key: 'learning', label: 'Learning', description: 'Measured outcome and lessons written to memory' },
]

export default function MissionControl({ stream }) {
  const { frames, latest, cycle, activity, approval, learning, outcome, mission, criticReview, thoughts, detection } =
    stream
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const wide = useMediaQuery('(min-width: 1400px)')
  const [active, lockSection] = useScrollSpy(SECTIONS.map((s) => s.key))

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

  const severity = detection?.suppressed ? 'NORMAL' : detection?.severity
  const alerting = severity === 'WARNING' || severity === 'CRITICAL'
  const markers = {
    analysis: alerting ? SEVERITY_COLOR[severity] : null,
    recovery: approval ? STATUS.warning : null,
    learning: learning ? STATUS.good : null,
  }

  const activityPanel = <AgentActivity activity={activity} fill={wide} />

  return (
    <>
      <nav className="section-nav" aria-label="Mission control sections">
        {SECTIONS.map((section) => (
          <a
            key={section.key}
            href={`#/control`}
            className={`section-link ${active === section.key ? 'active' : ''}`}
            onClick={(event) => {
              event.preventDefault()
              lockSection(section.key)
              document.getElementById(`mc-${section.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }}
          >
            {markers[section.key] && (
              <span className="dot" style={{ background: markers[section.key], boxShadow: `0 0 8px ${markers[section.key]}` }} />
            )}
            {section.label}
          </a>
        ))}
      </nav>

      {error && (
        <div className="banner" style={{ borderColor: STATUS.critical }} role="alert">
          <div className="banner-text">{error}</div>
          <button type="button" className="banner-close" aria-label="Dismiss" onClick={() => setError(null)}>
            ×
          </button>
        </div>
      )}

      <ApprovalBanner approval={approval} onDecide={decide} busy={busy} />

      <div className={wide ? 'mc-layout' : undefined}>
        <div style={{ minWidth: 0 }}>
          <Section section={SECTIONS[0]} index={1}>
            <MissionControls mission={mission} onError={setError} />
            <MissionTheater stream={stream} />
            {latest && <Vitals frame={latest} resources={cycle?.resources} />}
            <Panel
              title="ASTRIX loop"
              note="Detect · Remember · Diagnose · Assess · Plan · Simulate · Verify · Recover · Learn"
            >
              <LoopTrail cycle={cycle} learning={learning} />
            </Panel>
          </Section>

          <Section section={SECTIONS[1]} index={2}>
            {wide ? (
              <TelemetryCharts frames={frames} />
            ) : (
              <div className="grid main-split">
                <TelemetryCharts frames={frames} />
                {activityPanel}
              </div>
            )}
          </Section>

          <Section section={SECTIONS[2]} index={3}>
            <div className="grid cols-2">
              <DetectionPanel detection={cycle?.detection} />
              <MemoryPanel recall={cycle?.recall} />
            </div>
            <AgentThoughtInspector criticReview={criticReview} thoughts={thoughts} />
            <div className="grid cols-2">
              <DiagnosisPanel diagnosis={cycle?.diagnosis} />
              <RiskPanel risk={cycle?.risk} resources={cycle?.resources} />
            </div>
          </Section>

          <Section section={SECTIONS[3]} index={4}>
            <PlanPanel plan={cycle?.plan} selectedId={cycle?.safety?.action_id} />
            <WhatIfSandbox latestFrame={latest} currentCycle={cycle} />
            <div className="grid cols-2">
              <SimulationPanel simulation={cycle?.simulation} />
              <SafetyPanel safety={cycle?.safety} />
            </div>
          </Section>

          <Section section={SECTIONS[4]} index={5}>
            <LearningPanel learning={learning} outcome={outcome} />
          </Section>
        </div>

        {wide && <aside className="mc-rail">{activityPanel}</aside>}
      </div>
    </>
  )
}

function Section({ section, index, children }) {
  return (
    <section id={`mc-${section.key}`} className="section" aria-labelledby={`mc-${section.key}-title`}>
      <header className="section-head">
        <h2 id={`mc-${section.key}-title`}>
          <span className="index">{String(index).padStart(2, '0')}</span>
          {section.label}
        </h2>
        <p>{section.description}</p>
      </header>
      {children}
    </section>
  )
}

function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const list = window.matchMedia(query)
    const onChange = () => setMatches(list.matches)
    onChange()
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])
  return matches
}

// The active section is the last one whose top has scrolled under the sticky bars.
// Clicking a section link locks it active while the smooth scroll settles, so a
// short final section can't steal the highlight from the one the user picked.
function useScrollSpy(keys) {
  const [active, setActive] = useState(keys[0])
  const lock = useRef({ key: null, until: 0 })
  const joined = keys.join('|')
  useEffect(() => {
    let frame = null
    const update = () => {
      frame = null
      if (performance.now() < lock.current.until) {
        setActive(lock.current.key)
        return
      }
      const offset = 140
      let current = joined.split('|')[0]
      for (const key of joined.split('|')) {
        const el = document.getElementById(`mc-${key}`)
        if (el && el.getBoundingClientRect().top - offset <= 0) current = key
      }
      // At the bottom of the page the last section is active even if it is short.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        current = joined.split('|').at(-1)
      }
      setActive(current)
    }
    const onScroll = () => {
      if (frame == null) frame = requestAnimationFrame(update)
    }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame != null) cancelAnimationFrame(frame)
    }
  }, [joined])
  const lockSection = (key) => {
    lock.current = { key, until: performance.now() + 1200 }
    setActive(key)
  }
  return [active, lockSection]
}
