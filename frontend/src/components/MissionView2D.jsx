// 2D mission view: launch ascent, then the satellite in orbit.
//
// Two scenes on one canvas, chosen by mission phase:
//
//   Launch (COUNTDOWN → DEPLOYMENT) — side view over the curved Earth. The vehicle
//   flies the backend's ascent profile; the camera widens as it climbs, spent
//   stages and fairing halves fall away, and the satellite separates and unfolds
//   its arrays.
//
//   Orbit (ORBIT) — top-down view of the 500 km orbit. Satellite position comes from
//   the telemetry frame, the eclipse shadow, imaging, ground-contact and maneuver
//   arcs match the simulator's orbital schedule, and the satellite is coloured by
//   ASTRIX's current detection severity, not by the injected fault — the operator
//   knows what was injected; the view shows what ASTRIX has concluded.
//
// Snapshots arrive at 2–10 Hz; the canvas interpolates between them at display
// rate so motion is smooth regardless of the telemetry interval.

import { useEffect, useRef } from 'react'
import { INK, SERIES, SEVERITY_COLOR, STATUS } from '../theme'

const EARTH_R = 6371
const ORBIT_ALT = 500
const LAUNCH_PHASES = new Set(['COUNTDOWN', 'ASCENT', 'ORBIT_INSERTION', 'DEPLOYMENT'])

// Orbital schedule, as fractions of one orbit (mirrors telemetry/simulator.py).
const ARCS = [
  { from: 0.05, to: 0.26, color: SERIES[3], label: 'imaging' },
  { from: 0.3, to: 0.38, color: SERIES[2], label: 'ground contact' },
  { from: 0.44, to: 0.47, color: SERIES[1], label: 'slew' },
]
const ECLIPSE_FROM = 0.63

const COLORS = {
  space: '#0b0c10',
  earth: '#12324f',
  earthEdge: '#2f6fa3',
  land: '#1d4a36',
  atmosphere: 'rgba(90, 160, 255, 0.18)',
  night: 'rgba(0, 0, 0, 0.55)',
  vehicle: '#e8e6dc',
  stage: '#b9b6a8',
  flame: '#ffb347',
  trail: 'rgba(250, 178, 25, 0.75)',
  orbitRing: 'rgba(195, 194, 183, 0.25)',
}

const lerp = (a, b, t) => a + (b - a) * t
const clamp01 = (v) => Math.max(0, Math.min(1, v))

function useAnimationFrame(draw) {
  const drawRef = useRef(draw)
  drawRef.current = draw
  useEffect(() => {
    let id
    const loop = (now) => {
      drawRef.current(now)
      id = requestAnimationFrame(loop)
    }
    id = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(id)
  }, [])
}

// Keeps the previous and current value of a stream with the time it changed, so
// the renderer can interpolate between them.
function useInterpolated(value) {
  const ref = useRef({ prev: value, next: value, at: performance.now(), span: 400 })
  const current = ref.current
  if (value !== current.next) {
    const now = performance.now()
    const span = Math.min(1500, Math.max(60, now - current.at))
    ref.current = { prev: current.next ?? value, next: value, at: now, span }
  }
  return ref
}

function progress(ref, now) {
  const { at, span } = ref.current
  return clamp01((now - at) / span)
}

// ---------------------------------------------------------------- launch scene

function drawLaunch(ctx, w, h, snap, track, now) {
  ctx.fillStyle = COLORS.space
  ctx.fillRect(0, 0, w, h)
  drawStars(ctx, w, h)

  const alt = snap?.altitude_km ?? 0
  const down = snap?.downrange_km ?? 0

  // Camera: frame the vehicle with generous headroom, widening as it climbs.
  const span = Math.max(40, alt * 1.9, down * 1.25)
  const scale = Math.min(w, h * 1.4) / span
  const padX = w * 0.16
  const groundY = h * 0.82

  // Earth surface as a circle whose top touches the ground line at the pad.
  const earthRpx = EARTH_R * scale
  const cx = padX
  const cy = groundY + earthRpx

  const toScreen = (downrange, altitude) => {
    const theta = downrange / EARTH_R
    const r = (EARTH_R + altitude) * scale
    return [cx + r * Math.sin(theta), cy - r * Math.cos(theta)]
  }

  // Atmosphere glow, then Earth.
  const atmoPx = Math.max(6, 100 * scale)
  const glow = ctx.createRadialGradient(cx, cy, earthRpx, cx, cy, earthRpx + atmoPx)
  glow.addColorStop(0, 'rgba(90,160,255,0.35)')
  glow.addColorStop(1, 'rgba(90,160,255,0)')
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(cx, cy, earthRpx + atmoPx, 0, Math.PI * 2)
  ctx.fill()

  ctx.fillStyle = COLORS.earth
  ctx.beginPath()
  ctx.arc(cx, cy, earthRpx, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = COLORS.earthEdge
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Target orbit.
  ctx.save()
  ctx.setLineDash([6, 6])
  ctx.strokeStyle = COLORS.orbitRing
  ctx.beginPath()
  ctx.arc(cx, cy, (EARTH_R + ORBIT_ALT) * scale, -Math.PI / 2 - 0.05, -Math.PI / 2 + Math.PI / 2)
  ctx.stroke()
  ctx.restore()
  const labelPos = toScreen(Math.max(down * 0.5, span * 0.45), ORBIT_ALT)
  if (labelPos[1] > 12) {
    ctx.fillStyle = INK.muted
    ctx.font = '11px ui-monospace, monospace'
    ctx.fillText('500 km target orbit', labelPos[0], labelPos[1] - 6)
  }

  // Launch pad.
  const [padX2, padY] = toScreen(0, 0)
  ctx.fillStyle = INK.secondary
  ctx.fillRect(padX2 - 5, padY - 3, 10, 3)

  // Trajectory trail.
  if (track.length > 1) {
    ctx.strokeStyle = COLORS.trail
    ctx.lineWidth = 2
    ctx.beginPath()
    track.forEach(([d, a], i) => {
      const [x, y] = toScreen(d, a)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()
  }

  if (!snap) return

  const [vx, vy] = toScreen(down, alt)
  // Vehicle heading: flight path angle relative to local horizontal, plus the
  // rotation of local vertical around the Earth.
  const theta = down / EARTH_R
  const gamma = ((snap.flight_path_angle_deg ?? 90) * Math.PI) / 180
  const heading = theta + (Math.PI / 2 - gamma)

  // Separated hardware drifts away and fades after its milestone.
  const t = snap.t ?? 0
  if (t >= 153) drawDebris(ctx, vx, vy, heading, t - 153, 'stage1')
  if (t >= 205) drawDebris(ctx, vx, vy, heading, t - 205, 'fairing')
  if (t >= 560) drawDebris(ctx, vx, vy, heading, t - 560, 'upper')

  drawVehicle(ctx, vx, vy, heading, snap, now)
}

function drawStars(ctx, w, h) {
  ctx.fillStyle = 'rgba(255,255,255,0.35)'
  for (let i = 0; i < 70; i += 1) {
    const x = (i * 97.13) % w
    const y = (i * 53.71) % (h * 0.8)
    ctx.fillRect(x, y, 1, 1)
  }
}

function drawDebris(ctx, x, y, heading, age, kind) {
  const fade = clamp01(1 - age / 60)
  if (fade <= 0) return
  const drift = Math.min(80, age * 2.2)
  ctx.save()
  ctx.globalAlpha = fade
  ctx.translate(x, y)
  ctx.rotate(heading)
  if (kind === 'stage1') {
    ctx.translate(0, 26 + drift)
    ctx.rotate(age * 0.05)
    ctx.fillStyle = COLORS.stage
    ctx.fillRect(-4, -14, 8, 28)
  } else if (kind === 'fairing') {
    ctx.fillStyle = COLORS.vehicle
    ctx.save()
    ctx.translate(-6 - drift * 0.5, -18)
    ctx.rotate(-age * 0.04)
    ctx.fillRect(-2, -8, 3, 14)
    ctx.restore()
    ctx.translate(6 + drift * 0.5, -18)
    ctx.rotate(age * 0.04)
    ctx.fillRect(-1, -8, 3, 14)
  } else {
    ctx.translate(0, 10 + drift * 0.35)
    ctx.fillStyle = COLORS.stage
    ctx.fillRect(-3.5, -10, 7, 18)
  }
  ctx.restore()
}

function drawVehicle(ctx, x, y, heading, snap, now) {
  const stage = snap.stage ?? 0
  const throttle = snap.throttle_pct ?? 0
  ctx.save()
  ctx.translate(x, y)
  ctx.rotate(heading)

  if (stage >= 3) {
    drawSatelliteGlyph(ctx, snap.arrays_deployed ? 1 : clamp01(((snap.t ?? 560) - 560) / 30), STATUS.good)
    ctx.restore()
    return
  }

  // Exhaust plume flickers with throttle.
  if (throttle > 0) {
    const flicker = 0.85 + 0.15 * Math.sin(now / 45)
    const length = (10 + 26 * (snap.acceleration_g ?? 1) / 2.2) * flicker
    const base = stage === 1 ? 14 : 10
    const plume = ctx.createLinearGradient(0, base, 0, base + length)
    plume.addColorStop(0, 'rgba(255,240,200,0.95)')
    plume.addColorStop(0.4, 'rgba(255,179,71,0.8)')
    plume.addColorStop(1, 'rgba(255,90,40,0)')
    ctx.fillStyle = plume
    ctx.beginPath()
    ctx.moveTo(-3.5, base)
    ctx.lineTo(3.5, base)
    ctx.lineTo(0, base + length)
    ctx.closePath()
    ctx.fill()
  }

  // Stage 1 booster.
  if (stage <= 1) {
    ctx.fillStyle = COLORS.stage
    ctx.fillRect(-4, -2, 8, 16)
  }
  // Stage 2.
  ctx.fillStyle = COLORS.vehicle
  ctx.fillRect(-3.5, -12, 7, 10)
  // Fairing or exposed payload.
  if (snap.fairing_attached) {
    ctx.beginPath()
    ctx.moveTo(-3.5, -12)
    ctx.lineTo(3.5, -12)
    ctx.lineTo(0, -22)
    ctx.closePath()
    ctx.fill()
  } else {
    ctx.translate(0, -15)
    drawSatelliteGlyph(ctx, 0, STATUS.good, 0.6)
  }
  ctx.restore()
}

// Satellite body with solar arrays; `unfold` 0..1 opens the arrays.
function drawSatelliteGlyph(ctx, unfold, statusColor, size = 1) {
  ctx.save()
  ctx.scale(size, size)
  const span = 4 + 16 * unfold
  ctx.fillStyle = '#3d6fb8'
  ctx.strokeStyle = '#8fb7ee'
  ctx.lineWidth = 0.8
  ctx.fillRect(-4 - span, -3, span, 6)
  ctx.strokeRect(-4 - span, -3, span, 6)
  ctx.fillRect(4, -3, span, 6)
  ctx.strokeRect(4, -3, span, 6)
  ctx.fillStyle = '#d9d4c3'
  ctx.fillRect(-4, -5, 8, 10)
  ctx.fillStyle = statusColor
  ctx.beginPath()
  ctx.arc(0, 0, 2, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}

// ----------------------------------------------------------------- orbit scene

function orbitPhase(frame) {
  if (!frame?.position_km) return 0
  const [x, y] = frame.position_km
  const angle = Math.atan2(y, x)
  return ((angle / (2 * Math.PI)) % 1 + 1) % 1
}

function drawOrbit(ctx, w, h, frame, prevFrame, mix, severity, now, wheelsDisabled) {
  ctx.fillStyle = COLORS.space
  ctx.fillRect(0, 0, w, h)
  drawStars(ctx, w, h)

  const cx = w / 2
  const cy = h / 2
  const orbitPx = Math.min(w, h) * 0.4
  const earthPx = orbitPx * (EARTH_R / (EARTH_R + ORBIT_ALT)) * 0.8 // Earth drawn slightly small so arcs read clearly

  // Screen angle for an orbit fraction: phase 0 at the top, travelling clockwise.
  const angleOf = (f) => -Math.PI / 2 + f * 2 * Math.PI

  // Sun direction: opposite the centre of the eclipse arc.
  const shadowMid = (ECLIPSE_FROM + 1) / 2
  const shadowAngle = angleOf(shadowMid)

  // Shadow cylinder behind the Earth.
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate(shadowAngle)
  const shadow = ctx.createLinearGradient(0, 0, orbitPx * 1.4, 0)
  shadow.addColorStop(0, 'rgba(0,0,0,0.55)')
  shadow.addColorStop(1, 'rgba(0,0,0,0)')
  ctx.fillStyle = shadow
  ctx.fillRect(0, -earthPx, orbitPx * 1.4, earthPx * 2)
  ctx.restore()

  // Sun indicator.
  const sunX = cx - Math.cos(shadowAngle) * (orbitPx + 40)
  const sunY = cy - Math.sin(shadowAngle) * (orbitPx + 40)
  ctx.fillStyle = '#ffd36b'
  ctx.beginPath()
  ctx.arc(sunX, sunY, 5, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = INK.muted
  ctx.font = '10px ui-monospace, monospace'
  ctx.textAlign = 'center'
  ctx.fillText('SUN', sunX, sunY - 10)

  // Earth with a day/night terminator.
  const glow = ctx.createRadialGradient(cx, cy, earthPx, cx, cy, earthPx * 1.08)
  glow.addColorStop(0, 'rgba(90,160,255,0.35)')
  glow.addColorStop(1, 'rgba(90,160,255,0)')
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(cx, cy, earthPx * 1.08, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = COLORS.earth
  ctx.beginPath()
  ctx.arc(cx, cy, earthPx, 0, Math.PI * 2)
  ctx.fill()
  ctx.save()
  ctx.beginPath()
  ctx.arc(cx, cy, earthPx, 0, Math.PI * 2)
  ctx.clip()
  ctx.fillStyle = COLORS.land
  ;[
    [0.2, -0.3, 0.35],
    [-0.35, 0.2, 0.28],
    [0.3, 0.45, 0.2],
  ].forEach(([dx, dy, r]) => {
    ctx.beginPath()
    ctx.arc(cx + dx * earthPx, cy + dy * earthPx, r * earthPx, 0, Math.PI * 2)
    ctx.fill()
  })
  ctx.translate(cx, cy)
  ctx.rotate(shadowAngle)
  ctx.fillStyle = COLORS.night
  ctx.fillRect(0, -earthPx, earthPx, earthPx * 2)
  ctx.restore()
  ctx.strokeStyle = COLORS.earthEdge
  ctx.lineWidth = 1.2
  ctx.beginPath()
  ctx.arc(cx, cy, earthPx, 0, Math.PI * 2)
  ctx.stroke()

  // Orbit ring and schedule arcs.
  ctx.strokeStyle = COLORS.orbitRing
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.arc(cx, cy, orbitPx, 0, Math.PI * 2)
  ctx.stroke()

  ctx.lineWidth = 5
  ctx.lineCap = 'butt'
  ctx.strokeStyle = 'rgba(138,138,128,0.35)'
  ctx.beginPath()
  ctx.arc(cx, cy, orbitPx, angleOf(ECLIPSE_FROM), angleOf(1))
  ctx.stroke()
  ARCS.forEach((arc) => {
    ctx.strokeStyle = arc.color
    ctx.beginPath()
    ctx.arc(cx, cy, orbitPx, angleOf(arc.from), angleOf(arc.to))
    ctx.stroke()
  })

  // Ground station, under the middle of the contact arc.
  const gsAngle = angleOf(0.34)
  const gsX = cx + Math.cos(gsAngle) * earthPx
  const gsY = cy + Math.sin(gsAngle) * earthPx
  ctx.fillStyle = SERIES[2]
  ctx.fillRect(gsX - 3, gsY - 3, 6, 6)

  if (!frame) return

  // Satellite position, interpolated along the orbit.
  const a = orbitPhase(prevFrame ?? frame)
  let b = orbitPhase(frame)
  if (b < a - 0.5) b += 1
  const f = lerp(a, b, mix)
  const satAngle = angleOf(f)
  const sx = cx + Math.cos(satAngle) * orbitPx
  const sy = cy + Math.sin(satAngle) * orbitPx

  // Downlink beam while in contact.
  if (frame.ground_contact) {
    ctx.strokeStyle = 'rgba(25,158,112,0.7)'
    ctx.setLineDash([4, 4])
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(sx, sy)
    ctx.lineTo(gsX, gsY)
    ctx.stroke()
    ctx.setLineDash([])
  }

  const color = SEVERITY_COLOR[severity] ?? STATUS.good
  if (severity && severity !== 'NORMAL') {
    const pulse = 0.5 + 0.5 * Math.sin(now / 220)
    ctx.strokeStyle = color
    ctx.globalAlpha = 0.35 + 0.45 * pulse
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(sx, sy, 20 + 6 * pulse, 0, Math.PI * 2)
    ctx.stroke()
    ctx.globalAlpha = 1
  }

  ctx.save()
  ctx.translate(sx, sy)
  ctx.rotate(satAngle + Math.PI / 2)
  drawSatelliteGlyph(ctx, 1, color, 1.1)
  ctx.restore()

  // Reaction-wheel strip next to the satellite: one bar per wheel, height by
  // vibration, crossed out when isolated.
  const vib = [1, 2, 3, 4].map((n) => frame[`wheel_${n}_vibration`] ?? 0)
  const outward = satAngle
  const lx = sx + Math.cos(outward) * 34 - 16
  const ly = sy + Math.sin(outward) * 34
  vib.forEach((v, i) => {
    const bx = lx + i * 9
    const barH = Math.min(18, 3 + v * 4)
    const isolated = wheelsDisabled?.includes(i + 1)
    ctx.fillStyle = isolated ? INK.muted : v > 1.2 ? STATUS.warning : SERIES[0]
    ctx.fillRect(bx, ly - barH, 6, barH)
    if (isolated) {
      ctx.strokeStyle = STATUS.critical
      ctx.lineWidth = 1.2
      ctx.beginPath()
      ctx.moveTo(bx - 1, ly - 12)
      ctx.lineTo(bx + 7, ly)
      ctx.stroke()
    }
  })

  // Legend.
  ctx.textAlign = 'left'
  ctx.font = '10px ui-monospace, monospace'
  const legend = [...ARCS, { color: 'rgba(138,138,128,0.6)', label: 'eclipse' }]
  legend.forEach((item, i) => {
    const y = h - 14 - (legend.length - 1 - i) * 14
    ctx.fillStyle = item.color
    ctx.fillRect(12, y - 7, 12, 4)
    ctx.fillStyle = INK.muted
    ctx.fillText(item.label, 30, y - 2)
  })
}

// ----------------------------------------------------------------- component

export default function MissionView2D({ phase, launch, launchTrack, frames, severity, wheelsDisabled }) {
  const canvasRef = useRef(null)
  const sizeRef = useRef({ w: 0, h: 0 })
  const isLaunch = LAUNCH_PHASES.has(phase)

  const latest = frames.length ? frames[frames.length - 1] : null

  const launchInterp = useInterpolated(launch)
  const frameInterp = useInterpolated(latest)

  const stateRef = useRef({})
  stateRef.current = { isLaunch, launchTrack, severity, wheelsDisabled, phase }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      sizeRef.current = { w: width, h: height, dpr }
    })
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  useAnimationFrame((now) => {
    const canvas = canvasRef.current
    const { w, h, dpr } = sizeRef.current
    if (!canvas || !w || !h) return
    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const s = stateRef.current

    if (s.isLaunch) {
      const { prev, next } = launchInterp.current
      const mix = progress(launchInterp, now)
      const snap =
        prev && next
          ? {
              ...next,
              t: lerp(prev.t, next.t, mix),
              altitude_km: lerp(prev.altitude_km, next.altitude_km, mix),
              downrange_km: lerp(prev.downrange_km, next.downrange_km, mix),
              flight_path_angle_deg: lerp(prev.flight_path_angle_deg, next.flight_path_angle_deg, mix),
            }
          : next
      drawLaunch(ctx, w, h, snap, s.launchTrack, now)
    } else if (s.phase === 'ORBIT' || frameInterp.current.next) {
      const { prev, next } = frameInterp.current
      drawOrbit(ctx, w, h, next, prev, progress(frameInterp, now), s.severity, now, s.wheelsDisabled)
    } else {
      drawLaunch(ctx, w, h, null, [], now)
    }
  })

  return <canvas ref={canvasRef} className="mission-canvas" aria-label="2D mission view" />
}
