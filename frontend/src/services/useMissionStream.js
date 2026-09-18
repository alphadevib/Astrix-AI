// Live mission state, assembled from the WebSocket feed.
//
// The backend emits granular per-stage events (for the activity feed) plus one
// consolidated `cycle` event per anomaly cycle. The cycle event is what drives the
// analysis panels: taking each stage from its own event would eventually render a
// diagnosis from one cycle beside a simulation from the next, which is exactly the
// kind of inconsistency a mission-control screen must never show.
//
// It also keeps the fault timeline — frame of injection, first detection, first
// alarm, diagnosis, decision, outcome — measured in spacecraft frames, so the
// dashboard can show how fast Astrix reacted rather than just that it did.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api, { apiBase } from './api'
import session from './auth'

const MAX_FRAMES = 240
const MAX_ACTIVITY = 120
const MAX_LAUNCH_TRACK = 2000

// Events shown in the agent activity feed, in the order the loop produces them.
const ACTIVITY_TYPES = new Set([
  'mission_started',
  'launch_milestone',
  'orbit_acquired',
  'mission_stopped',
  'fault_injected',
  'fault_cleared',
  'detection',
  'suppressed',
  'diagnosis',
  'risk',
  'plan',
  'simulation',
  'safety',
  'approval_required',
  'approval',
  'approval_expired',
  'executed',
  'command_applied',
  'outcome',
  'learning',
  'anomaly_closed',
  'runner_error',
])

const EMPTY_TIMELINE = null

// Browsers cannot set headers on a WebSocket, so the session token rides in the
// query string. The server rejects the socket before accepting it without one.
function socketUrl() {
  const base = apiBase()
  const query = `?token=${encodeURIComponent(session.token())}`
  if (base) {
    return `${base.replace(/^http/, 'ws')}/ws/telemetry${query}`
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/telemetry${query}`
}

export function useMissionStream() {
  const [connected, setConnected] = useState(false)
  const [health, setHealth] = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [mission, setMission] = useState({ running: false, phase: 'IDLE' })
  const [frames, setFrames] = useState([])
  const [cycle, setCycle] = useState(null)
  const [activity, setActivity] = useState([])
  const [approval, setApproval] = useState(null)
  const [learning, setLearning] = useState(null)
  const [outcome, setOutcome] = useState(null)
  const [detection, setDetection] = useState(null)
  const [launch, setLaunch] = useState(null)
  const [launchTrack, setLaunchTrack] = useState([])
  const [milestones, setMilestones] = useState([])
  const [timeline, setTimeline] = useState(EMPTY_TIMELINE)
  const [criticReview, setCriticReview] = useState(null)
  const [thoughts, setThoughts] = useState([])

  const socketRef = useRef(null)
  const retryRef = useRef(null)
  const frameBufferRef = useRef([])
  const flushTimerRef = useRef(null)
  // Latest frame seq, readable synchronously inside the event handler. Events for a
  // frame arrive after that frame's telemetry on the same ordered socket.
  const seqRef = useRef(null)

  const pushActivity = useCallback((event) => {
    if (!ACTIVITY_TYPES.has(event.type)) return
    setActivity((previous) => [event, ...previous].slice(0, MAX_ACTIVITY))
  }, [])

  const markTimeline = useCallback((key, extra = {}) => {
    setTimeline((current) => {
      if (!current || current[key]) return current
      return { ...current, [key]: { seq: seqRef.current, at: Date.now(), ...extra } }
    })
  }, [])

  const resetMission = useCallback(() => {
    frameBufferRef.current = []
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    setFrames([])
    setCycle(null)
    setApproval(null)
    setLearning(null)
    setOutcome(null)
    setDetection(null)
    setLaunch(null)
    setLaunchTrack([])
    setMilestones([])
    setTimeline(EMPTY_TIMELINE)
    setCriticReview(null)
    setThoughts([])
    seqRef.current = null
  }, [])

  const handle = useCallback(
    (event) => {
      const p = event.payload ?? {}
      if (event.replay) {
        pushActivity(event)
        return
      }
      switch (event.type) {
        case 'hello':
          // The server replays recent activity right after this; start the feed
          // from that replay instead of appending duplicates on every reconnect.
          setActivity([])
          setHealth(p.health)
          setPipeline(p.pipeline)
          if (p.mission) {
            setMission(p.mission)
            setLaunch(p.mission.launch ?? null)
            setMilestones(p.mission.milestones ?? [])
          }
          break
        case 'mission_started':
          resetMission()
          setMission((m) => ({ ...m, running: true, phase: p.include_launch ? 'COUNTDOWN' : 'DEPLOYMENT' }))
          break
        case 'mission_stopped':
          resetMission()
          setMission((m) => ({ ...m, running: false, phase: 'STOPPED' }))
          break
        case 'launch':
          setLaunch(p)
          setMission((m) => (m.phase === p.phase ? m : { ...m, phase: p.phase }))
          setLaunchTrack((track) => {
            const next = [...track, [p.downrange_km, p.altitude_km]]
            return next.length > MAX_LAUNCH_TRACK ? next.slice(next.length - MAX_LAUNCH_TRACK) : next
          })
          break
        case 'launch_milestone':
          setMilestones((list) => (list.some((m) => m.key === p.key) ? list : [...list, p]))
          break
        case 'orbit_acquired':
          setMission((m) => ({ ...m, phase: 'ORBIT' }))
          break
        case 'telemetry':
          seqRef.current = p.seq
          frameBufferRef.current.push(p)
          if (!flushTimerRef.current) {
            flushTimerRef.current = setTimeout(() => {
              flushTimerRef.current = null
              const incoming = frameBufferRef.current
              frameBufferRef.current = []
              if (incoming.length > 0) {
                setFrames((prev) => {
                  const combined = prev.concat(incoming)
                  return combined.length > MAX_FRAMES ? combined.slice(combined.length - MAX_FRAMES) : combined
                })
              }
            }, 100) // smooth 10 Hz UI refresh window
          }
          break
        case 'critic_review':
          setCriticReview(p)
          break
        case 'agent_thought':
          setThoughts((prev) => [p, ...prev].slice(0, 30))
          break
        case 'detection':
          setDetection(p)
          if (p.is_anomaly && !p.suppressed) {
            markTimeline('detected', { severity: p.severity })
            if (p.severity === 'WARNING' || p.severity === 'CRITICAL') {
              markTimeline('alarm', { severity: p.severity })
            }
          }
          break
        case 'suppressed':
          markTimeline('suppressed', { reason: p.reason })
          break
        case 'diagnosis':
          markTimeline('diagnosed', { subsystem: p.subsystem, cause: p.probable_cause })
          break
        case 'cycle':
          setCycle(p)
          break
        case 'approval_required':
          setApproval(p)
          markTimeline('decided', { action: p.action_id, mode: 'awaiting approval' })
          break
        case 'approval':
        case 'approval_expired':
          // Only clear the banner for the request this event resolves; a decision
          // on a different anomaly must not hide a live approval request.
          setApproval((current) =>
            current && current.anomaly_id === p.anomaly_id ? null : current,
          )
          break
        case 'executed':
          setApproval((current) =>
            current && current.anomaly_id === p.anomaly_id ? null : current,
          )
          markTimeline('decided', { action: p.action_id, mode: p.operator })
          markTimeline('executed', { action: p.action_id, operator: p.operator })
          break
        case 'learning':
          setLearning(p)
          break
        case 'outcome':
          setOutcome(p)
          markTimeline('outcome', { outcome: p.outcome, effectiveness: p.effectiveness })
          break
        case 'fault_injected':
          setTimeline({
            scenario: p.scenario,
            title: p.title,
            subsystem: p.subsystem,
            benign: p.is_fault === false,
            injected: { seq: p.seq ?? seqRef.current, at: Date.now() },
          })
          setMission((m) => ({ ...m, active_scenario: p.scenario, active_scenario_title: p.title }))
          break
        case 'fault_cleared':
          setMission((m) => ({ ...m, active_scenario: null, active_scenario_title: null }))
          break
        default:
          break
      }
      pushActivity(event)
    },
    [pushActivity, markTimeline, resetMission],
  )

  // --- connection, with simple fixed-delay retry ---
  useEffect(() => {
    let disposed = false

    const connect = () => {
      if (disposed) return
      const socket = new WebSocket(socketUrl())
      socketRef.current = socket

      socket.onopen = () => setConnected(true)
      socket.onmessage = (message) => {
        try {
          handle(JSON.parse(message.data))
        } catch {
          /* ignore a malformed frame rather than tearing down the socket */
        }
      }
      socket.onclose = () => {
        setConnected(false)
        if (!disposed) {
          retryRef.current = window.setTimeout(connect, 1500)
        }
      }
      socket.onerror = () => socket.close()
    }

    connect()
    return () => {
      disposed = true
      if (retryRef.current) window.clearTimeout(retryRef.current)
      socketRef.current?.close()
    }
  }, [handle])

  // Seed the charts from the rolling window so a page refresh mid-demo does not
  // start from an empty plot.
  useEffect(() => {
    let cancelled = false
    api
      .history(MAX_FRAMES)
      .then((data) => {
        if (!cancelled && data.frames?.length) {
          setFrames(data.frames)
          seqRef.current = data.frames[data.frames.length - 1].seq
        }
      })
      .catch(() => {})
    api
      .pendingApprovals()
      .then((data) => {
        if (!cancelled && data.pending?.length) {
          const [first] = data.pending
          setApproval({
            anomaly_id: first.anomaly_id,
            action_id: first.action_id,
            description: first.description,
            risk_level: first.risk_level,
          })
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Periodic status poll: covers runner state that is not itself an event
  // (wheels disabled, safe mode, a runner that died) and self-heals after a
  // dropped socket.
  useEffect(() => {
    let cancelled = false
    const tick = () =>
      api
        .status()
        .then((data) => {
          if (cancelled) return
          setPipeline(data.pipeline)
          setMission(data.mission)
          // The server's queue is the source of truth: restore a request the
          // socket missed, and drop one that was resolved while disconnected.
          const queue = data.pipeline?.awaiting_approval
          if (Array.isArray(queue)) {
            setApproval((current) => {
              if (!queue.length) return null
              if (current && queue.some((q) => q.anomaly_id === current.anomaly_id)) return current
              const [first] = queue
              return {
                anomaly_id: first.anomaly_id,
                action_id: first.action_id,
                description: first.description,
                risk_level: first.risk_level,
              }
            })
          }
        })
        .catch(() => {})
    tick()
    const id = window.setInterval(tick, 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  const latest = frames.length ? frames[frames.length - 1] : null

  const reasoner = useMemo(() => {
    if (health?.llm?.available) return { label: `LLM · ${health.llm.model}`, llm: true }
    return { label: 'Deterministic', llm: false, reason: health?.llm?.reason }
  }, [health])

  return {
    connected,
    health,
    pipeline,
    mission,
    frames,
    latest,
    cycle,
    activity,
    approval,
    learning,
    outcome,
    detection,
    launch,
    launchTrack,
    milestones,
    timeline,
    reasoner,
    criticReview,
    thoughts,
    setApproval,
  }
}

export default useMissionStream
