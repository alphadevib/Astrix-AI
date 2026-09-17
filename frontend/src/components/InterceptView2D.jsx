// 2D engagement view for the interceptor trajectory check.
//
// Side view (downrange × altitude, equal km scale). Draws the pre-flight predicted
// trajectory with its corridor, the inbound object's path, and the three views
// of the interceptor that the health monitor compares:
//
//   radar     independent ground radar track        (aqua dots)
//   reported  the vehicle's own downlinked nav       (yellow dashed)
//   truth     where it really is — simulator only    (blue)
//
// Under GNSS spoofing or a replay attack `reported` peels away from `radar`;
// under forged target updates the vehicle's aim point (hollow ✕) moves to a ghost.
//
// Interaction: scroll to zoom, drag to pan, double-click to reset. Drag the
// target's start point or the tip of its velocity arrow to re-plan the
// engagement. Hover the trajectory for a readout; click it (or an alert marker)
// to seek playback to that moment.

import { useEffect, useRef } from 'react'
import { INK, SERIES, SEVERITY_COLOR, STATUS } from '../theme'
import { CanvasToolbar } from './MissionView2D'
import useCanvasViewport, { drawTooltip, pickHit } from './useCanvasViewport'

const SAMPLE_DT = 0.2
const VELOCITY_ARROW_S = 15
const LEVEL_RANK = { NOMINAL: 0, WATCH: 1, WARNING: 2, CRITICAL: 3 }

export function sampleAt(samples, t) {
  if (!samples?.length) return null
  const i = Math.max(0, Math.min(samples.length - 1, Math.round(t / SAMPLE_DT)))
  return samples[i]
}

export function worstLevel(checks) {
  let worst = 'NOMINAL'
  for (const level of Object.values(checks ?? {})) if (LEVEL_RANK[level] > LEVEL_RANK[worst]) worst = level
  return worst
}

function bounds(result, config) {
  let xmax = Math.max(200, config.target_x_km + 25)
  let ymax = Math.max(90, config.target_y_km + 20)
  for (const s of result?.samples ?? []) {
    xmax = Math.max(xmax, s.target[0] + 10)
    ymax = Math.max(ymax, s.target[1] + 10, s.truth[1] + 10)
  }
  return { xmin: -12, xmax, ymin: -6, ymax }
}

export const LAYERS = [
  ['corridor', 'predicted'],
  ['truth', 'truth'],
  ['radar', 'radar'],
  ['reported', 'nav'],
  ['estimate', 'aim point'],
]

export default function InterceptView2D({
  result,
  config,
  time,
  layers,
  onToggleLayer,
  onSeek,
  onTargetChange,
  checkLabels,
}) {
  const canvasRef = useRef(null)
  const sizeRef = useRef({ w: 0, h: 0, dpr: 1 })
  const mapRef = useRef(null)
  const hitsRef = useRef([])
  const dragRef = useRef(null)

  const props = useRef({})
  props.current = { result, config, time, layers, onSeek, onTargetChange, checkLabels }

  const viewport = useCanvasViewport(canvasRef, {
    onDragStart: (bx, by) => {
      const hit = pickHit(hitsRef.current, bx, by, viewport.view.current.scale)
      if (hit?.kind !== 'handle' || !mapRef.current) return false
      const { config: cfg } = props.current
      dragRef.current = {
        handle: hit.handle,
        x: cfg.target_x_km,
        y: cfg.target_y_km,
        vx: cfg.target_vx_kms,
        vy: cfg.target_vy_kms,
      }
      return true
    },
    onDrag: (bx, by) => {
      const drag = dragRef.current
      const map = mapRef.current
      if (!drag || !map) return
      const [wx, wy] = map.toWorld(bx, by)
      if (drag.handle === 'start') {
        drag.x = clamp(wx, 20, 290)
        drag.y = clamp(wy, 5, 145)
      } else {
        drag.vx = clamp((wx - drag.x) / VELOCITY_ARROW_S, -5, -0.3)
        drag.vy = clamp((wy - drag.y) / VELOCITY_ARROW_S, -3, 3)
      }
    },
    onDragEnd: () => {
      const drag = dragRef.current
      dragRef.current = null
      if (!drag) return
      props.current.onTargetChange?.({
        target_x_km: round(drag.x, 1),
        target_y_km: round(drag.y, 1),
        target_vx_kms: round(drag.vx, 2),
        target_vy_kms: round(drag.vy, 2),
      })
    },
    onClick: (bx, by) => {
      const hit = pickHit(hitsRef.current, bx, by, viewport.view.current.scale)
      if (hit?.t != null) props.current.onSeek?.(hit.t)
    },
  })

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

  useEffect(() => {
    let id
    const loop = (now) => {
      draw(now)
      id = requestAnimationFrame(loop)
    }
    id = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function draw(now) {
    const canvas = canvasRef.current
    const { w, h, dpr } = sizeRef.current
    if (!canvas || !w || !h) return
    const ctx = canvas.getContext('2d')
    const { result: res, config: cfg, time: t, layers: lay, checkLabels: labels } = props.current

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const sky = ctx.createLinearGradient(0, 0, 0, h)
    sky.addColorStop(0, '#07080c')
    sky.addColorStop(1, '#101824')
    ctx.fillStyle = sky
    ctx.fillRect(0, 0, w, h)

    // World (km) → base screen mapping, equal scale on both axes.
    const b = bounds(res, cfg)
    const pad = 28
    const k = Math.min((w - pad * 2) / (b.xmax - b.xmin), (h - pad * 2) / (b.ymax - b.ymin))
    const offX = (w - (b.xmax - b.xmin) * k) / 2
    const offY = (h - (b.ymax - b.ymin) * k) / 2
    const X = (x) => offX + (x - b.xmin) * k
    const Y = (y) => h - offY - (y - b.ymin) * k
    const P = ([x, y]) => [X(x), Y(y)]
    mapRef.current = { toWorld: (bx, by) => [(bx - offX) / k + b.xmin, (h - offY - by) / k + b.ymin] }

    viewport.apply(ctx, dpr)
    const scale = viewport.view.current.scale
    const px = (n) => n / scale // constant on-screen size regardless of zoom
    const hits = []

    // Ground, grid and keep-out band.
    ctx.fillStyle = '#0d1a12'
    ctx.fillRect(X(b.xmin) - 2000, Y(0), 4000 + (b.xmax - b.xmin) * k, 2000)
    ctx.strokeStyle = 'rgba(195,194,183,0.07)'
    ctx.lineWidth = px(1)
    ctx.font = `${px(10)}px ui-monospace, monospace`
    ctx.fillStyle = INK.muted
    for (let gx = 0; gx <= b.xmax; gx += 20) {
      ctx.beginPath()
      ctx.moveTo(X(gx), Y(0))
      ctx.lineTo(X(gx), Y(b.ymax))
      ctx.stroke()
      ctx.textAlign = 'center'
      ctx.fillText(`${gx}`, X(gx), Y(0) + px(13))
    }
    for (let gy = 20; gy <= b.ymax; gy += 20) {
      ctx.beginPath()
      ctx.moveTo(X(0), Y(gy))
      ctx.lineTo(X(b.xmax), Y(gy))
      ctx.stroke()
      ctx.textAlign = 'right'
      ctx.fillText(`${gy}`, X(0) - px(5), Y(gy) + px(3))
    }
    ctx.textAlign = 'left'
    ctx.textAlign = 'right'
    ctx.fillText('downrange km →', X(b.xmax), Y(0) - px(6))
    ctx.textAlign = 'left'
    ctx.fillText('↑ altitude km', X(0) + px(4), Y(b.ymax) + px(10))

    const limits = res?.limits ?? { min_intercept_alt_km: 8, max_intercept_range_km: 160 }
    ctx.fillStyle = 'rgba(208,59,59,0.08)'
    ctx.fillRect(X(0), Y(limits.min_intercept_alt_km), (b.xmax - 0) * k, limits.min_intercept_alt_km * k)
    ctx.fillStyle = 'rgba(236,131,90,0.7)'
    ctx.fillText(`intercept keep-out < ${limits.min_intercept_alt_km} km`, X(limits.max_intercept_range_km) + px(6), Y(limits.min_intercept_alt_km) + px(12))
    ctx.save()
    ctx.setLineDash([px(4), px(5)])
    ctx.strokeStyle = 'rgba(236,131,90,0.35)'
    ctx.beginPath()
    ctx.moveTo(X(limits.max_intercept_range_km), Y(0))
    ctx.lineTo(X(limits.max_intercept_range_km), Y(b.ymax))
    ctx.stroke()
    ctx.restore()
    hits.push({
      x: X(limits.max_intercept_range_km),
      y: Y(b.ymax * 0.5),
      r: 8,
      lines: ['Max intercept range', `${limits.max_intercept_range_km} km`],
    })

    // Defended site / launcher.
    ctx.fillStyle = SERIES[0]
    ctx.beginPath()
    ctx.moveTo(X(0), Y(0) - px(9))
    ctx.lineTo(X(0) - px(6), Y(0))
    ctx.lineTo(X(0) + px(6), Y(0))
    ctx.closePath()
    ctx.fill()
    hits.push({ x: X(0), y: Y(0) - px(4), r: 10, lines: ['Launch site / defended area', `launch at T+${cfg.launch_delay_s} s`] })

    if (!res) {
      drawHandles(ctx, cfg, X, Y, px, hits, null)
      finish(ctx, dpr, w, h, hits, scale)
      return
    }

    const samples = res.samples
    const current = sampleAt(samples, t)
    const upto = samples.filter((s) => s.t <= t + 1e-6)
    const flown = upto.filter((s) => s.launched)

    // Pre-flight prediction and corridor.
    const nominal = res.nominal.filter((s) => s.launched)
    if (lay.corridor && nominal.length > 1) {
      ctx.save()
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.strokeStyle = 'rgba(195,194,183,0.08)'
      ctx.lineWidth = 2 * k // ±1 km warning corridor
      polyline(ctx, nominal.map((s) => [X(s.x), Y(s.y)]))
      ctx.strokeStyle = 'rgba(195,194,183,0.45)'
      ctx.lineWidth = px(1.2)
      ctx.setLineDash([px(5), px(5)])
      polyline(ctx, nominal.map((s) => [X(s.x), Y(s.y)]))
      ctx.restore()
    }

    // Target path: whole predicted path faint, flown part solid.
    ctx.save()
    ctx.strokeStyle = 'rgba(217,89,38,0.25)'
    ctx.setLineDash([px(3), px(4)])
    ctx.lineWidth = px(1)
    polyline(ctx, samples.map((s) => P(s.target)))
    ctx.restore()
    ctx.strokeStyle = SERIES[1]
    ctx.lineWidth = px(1.8)
    polyline(ctx, upto.map((s) => P(s.target)))

    if (lay.truth && flown.length > 1) {
      ctx.strokeStyle = SERIES[0]
      ctx.lineWidth = px(2)
      polyline(ctx, flown.map((s) => P(s.truth)))
    }
    if (lay.reported && flown.length > 1) {
      ctx.save()
      ctx.strokeStyle = SERIES[3]
      ctx.lineWidth = px(1.5)
      ctx.setLineDash([px(6), px(4)])
      polyline(ctx, flown.map((s) => P(s.reported)))
      ctx.restore()
    }
    if (lay.radar) {
      ctx.fillStyle = SERIES[2]
      flown.forEach((s, i) => {
        if (i % 2) return
        const [x, y] = P(s.radar)
        ctx.fillRect(x - px(1.2), y - px(1.2), px(2.4), px(2.4))
      })
    }

    // Trajectory hover targets: every other sample along the radar track.
    for (let i = 0; i < flown.length; i += 2) {
      const s = flown[i]
      const [x, y] = P(s.radar)
      hits.push({ x, y, r: 5, t: s.t, sample: s })
    }

    // Predicted intercept point from the trajectory check.
    const pip = res.preflight.predicted.point
    if (pip) {
      const [x, y] = P(pip)
      ctx.strokeStyle = INK.secondary
      ctx.lineWidth = px(1)
      ctx.beginPath()
      ctx.arc(x, y, px(7), 0, Math.PI * 2)
      ctx.moveTo(x - px(11), y)
      ctx.lineTo(x + px(11), y)
      ctx.moveTo(x, y - px(11))
      ctx.lineTo(x, y + px(11))
      ctx.stroke()
      hits.push({
        x,
        y,
        r: 11,
        t: res.preflight.predicted.t_intercept,
        color: INK.secondary,
        lines: [
          'Predicted intercept point',
          `T+${res.preflight.predicted.t_intercept?.toFixed(1)} s · ${pip[0].toFixed(1)} km, ${pip[1].toFixed(1)} km alt`,
          `time of flight ${res.preflight.predicted.time_of_flight_s} s`,
          'click to seek',
        ],
      })
    }

    // Fault onset.
    if (res.fault && res.config.onset_s != null) {
      const s = sampleAt(samples, res.config.onset_s)
      if (s?.launched && s.t <= t) {
        const [x, y] = P(s.radar)
        ctx.strokeStyle = STATUS.serious
        ctx.lineWidth = px(1.5)
        ctx.beginPath()
        ctx.moveTo(x - px(6), y - px(6))
        ctx.lineTo(x + px(6), y + px(6))
        ctx.moveTo(x + px(6), y - px(6))
        ctx.lineTo(x - px(6), y + px(6))
        ctx.stroke()
        hits.push({
          x,
          y,
          r: 9,
          t: s.t,
          color: STATUS.serious,
          lines: [`${res.fault.category} anomaly onset`, res.fault.title, `T+${s.t.toFixed(1)} s`],
        })
      }
    }

    // Alert markers.
    res.events.forEach((event) => {
      if (event.t > t) return
      const s = sampleAt(samples, event.t)
      if (!s) return
      const [x, y] = P(s.radar)
      const color = SEVERITY_COLOR[event.level]
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(x, y, px(event.level === 'CRITICAL' ? 5 : 4), 0, Math.PI * 2)
      ctx.fill()
      ctx.strokeStyle = '#0b0c10'
      ctx.lineWidth = px(1)
      ctx.stroke()
      hits.push({
        x,
        y,
        r: 8,
        t: event.t,
        color,
        lines: [
          `${event.level} · ${labels?.[event.check] ?? event.check}`,
          `T+${event.t.toFixed(1)} s · value ${event.value}`,
          'click to seek',
        ],
      })
    })

    // Current state at the playhead.
    if (current) {
      const [tx, ty] = P(current.target)
      ctx.fillStyle = SERIES[1]
      ctx.beginPath()
      ctx.moveTo(tx, ty - px(6))
      ctx.lineTo(tx + px(6), ty)
      ctx.lineTo(tx, ty + px(6))
      ctx.lineTo(tx - px(6), ty)
      ctx.closePath()
      ctx.fill()
      hits.push({
        x: tx,
        y: ty,
        r: 9,
        color: SERIES[1],
        lines: ['Inbound object', `${current.target[0].toFixed(1)} km downrange · ${current.target[1].toFixed(1)} km alt`],
      })

      if (current.launched) {
        const worst = worstLevel(current.checks)
        const color = SEVERITY_COLOR[worst]
        const prev = sampleAt(samples, Math.max(0, current.t - SAMPLE_DT))
        const heading = Math.atan2(-(current.truth[1] - prev.truth[1]), current.truth[0] - prev.truth[0])

        if (lay.estimate) {
          const [ex, ey] = P(current.target_est)
          const [vx0, vy0] = P(current.reported)
          ctx.save()
          ctx.strokeStyle = 'rgba(217,89,38,0.55)'
          ctx.setLineDash([px(2), px(3)])
          ctx.lineWidth = px(1)
          ctx.beginPath()
          ctx.moveTo(vx0, vy0)
          ctx.lineTo(ex, ey)
          ctx.stroke()
          ctx.restore()
          ctx.strokeStyle = SERIES[1]
          ctx.lineWidth = px(1.5)
          ctx.beginPath()
          ctx.moveTo(ex - px(5), ey - px(5))
          ctx.lineTo(ex + px(5), ey + px(5))
          ctx.moveTo(ex + px(5), ey - px(5))
          ctx.lineTo(ex - px(5), ey + px(5))
          ctx.stroke()
          hits.push({
            x: ex,
            y: ey,
            r: 8,
            color: SERIES[1],
            lines: [
              "Vehicle's target estimate (aim point)",
              `off ground track by ${dist(current.target_est, current.target_track).toFixed(2)} km`,
            ],
          })
        }
        if (lay.reported) {
          const [rx, ry] = P(current.reported)
          ctx.strokeStyle = SERIES[3]
          ctx.lineWidth = px(1.5)
          ctx.beginPath()
          ctx.arc(rx, ry, px(5), 0, Math.PI * 2)
          ctx.stroke()
        }

        const [ix, iy] = P(lay.truth ? current.truth : current.radar)
        if (worst !== 'NOMINAL') {
          const pulse = 0.5 + 0.5 * Math.sin(now / 200)
          ctx.strokeStyle = color
          ctx.globalAlpha = 0.3 + 0.5 * pulse
          ctx.lineWidth = px(2)
          ctx.beginPath()
          ctx.arc(ix, iy, px(12 + 4 * pulse), 0, Math.PI * 2)
          ctx.stroke()
          ctx.globalAlpha = 1
        }
        ctx.save()
        ctx.translate(ix, iy)
        ctx.rotate(heading)
        ctx.fillStyle = '#e8e6dc'
        ctx.beginPath()
        ctx.moveTo(px(9), 0)
        ctx.lineTo(px(-6), px(-4))
        ctx.lineTo(px(-3), 0)
        ctx.lineTo(px(-6), px(4))
        ctx.closePath()
        ctx.fill()
        ctx.restore()
        hits.push({ x: ix, y: iy, r: 12, t: current.t, sample: current, color })
      }
    }

    // Outcome at closest approach.
    const outcome = res.outcome
    if (outcome.point && outcome.t <= t + SAMPLE_DT) {
      const [ox, oy] = P(outcome.point)
      const hit = outcome.result === 'INTERCEPT'
      const color = hit ? STATUS.good : STATUS.critical
      ctx.strokeStyle = color
      ctx.lineWidth = px(2)
      if (hit) {
        for (let i = 0; i < 8; i += 1) {
          const a = (i / 8) * Math.PI * 2
          ctx.beginPath()
          ctx.moveTo(ox + Math.cos(a) * px(5), oy + Math.sin(a) * px(5))
          ctx.lineTo(ox + Math.cos(a) * px(13), oy + Math.sin(a) * px(13))
          ctx.stroke()
        }
      } else {
        ctx.beginPath()
        ctx.arc(ox, oy, px(10), 0, Math.PI * 2)
        ctx.stroke()
      }
      ctx.font = `${px(11)}px ui-monospace, monospace`
      ctx.fillStyle = color
      ctx.textAlign = 'left'
      ctx.fillText(
        `${outcome.result} · miss ${(outcome.miss_km * 1000).toFixed(0)} m`,
        ox + px(16),
        oy - px(10),
      )
      hits.push({
        x: ox,
        y: oy,
        r: 14,
        t: outcome.t,
        color,
        lines: [
          outcome.result,
          `closest approach ${(outcome.miss_km * 1000).toFixed(0)} m at T+${outcome.t.toFixed(1)} s`,
          `lethal radius ${(outcome.lethal_radius_km * 1000).toFixed(0)} m`,
        ],
      })
    }

    drawHandles(ctx, cfg, X, Y, px, hits, dragRef.current)
    finish(ctx, dpr, w, h, hits, scale)
  }

  function finish(ctx, dpr, w, h, hits, scale) {
    hitsRef.current = hits
    const canvas = canvasRef.current
    const pointer = viewport.pointer.current
    let hovered = null
    if (pointer.inside && !pointer.down) {
      const [bx, by] = viewport.toBase(pointer.x, pointer.y)
      // Prefer discrete markers over the trajectory samples beneath them.
      const markers = hits.filter((hit) => !hit.sample || hit.lines)
      hovered = pickHit(markers, bx, by, scale) ?? pickHit(hits, bx, by, scale)
      if (hovered) {
        ctx.strokeStyle = hovered.color ?? INK.secondary
        ctx.lineWidth = 1.2 / scale
        ctx.beginPath()
        ctx.arc(hovered.x, hovered.y, (hovered.r ?? 8) / Math.min(scale, 3) + 3 / scale, 0, Math.PI * 2)
        ctx.stroke()
      }
    }
    canvas.style.cursor = pointer.down
      ? pointer.mode === 'handle'
        ? 'move'
        : 'grabbing'
      : hovered?.kind === 'handle'
        ? 'move'
        : hovered?.t != null
          ? 'pointer'
          : 'grab'

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    drawLegend(ctx, h, props.current.layers)
    if (hovered) {
      const lines = hovered.lines ?? sampleLines(hovered.sample, props.current.checkLabels)
      drawTooltip(ctx, pointer.x, pointer.y, w, h, lines, hovered.color)
    }
  }

  return (
    <>
      <canvas
        ref={canvasRef}
        className="mission-canvas"
        aria-label="Interceptor engagement view — drag the target handles to re-plan, click the trajectory to seek"
      />
      <CanvasToolbar
        layers={layers}
        layerLabels={LAYERS}
        onToggleLayer={onToggleLayer}
        onZoom={viewport.zoomBy}
        onReset={viewport.reset}
      />
    </>
  )
}

function drawHandles(ctx, cfg, X, Y, px, hits, drag) {
  const x0 = drag?.x ?? cfg.target_x_km
  const y0 = drag?.y ?? cfg.target_y_km
  const vx = drag?.vx ?? cfg.target_vx_kms
  const vy = drag?.vy ?? cfg.target_vy_kms
  const [sx, sy] = [X(x0), Y(y0)]
  const [ex, ey] = [X(x0 + vx * VELOCITY_ARROW_S), Y(y0 + vy * VELOCITY_ARROW_S)]

  ctx.save()
  ctx.strokeStyle = drag ? '#ffffff' : 'rgba(217,89,38,0.9)'
  ctx.fillStyle = drag ? '#ffffff' : SERIES[1]
  ctx.lineWidth = px(1.5)
  ctx.beginPath()
  ctx.moveTo(sx, sy)
  ctx.lineTo(ex, ey)
  ctx.stroke()
  const a = Math.atan2(ey - sy, ex - sx)
  ctx.beginPath()
  ctx.moveTo(ex, ey)
  ctx.lineTo(ex - Math.cos(a - 0.4) * px(9), ey - Math.sin(a - 0.4) * px(9))
  ctx.lineTo(ex - Math.cos(a + 0.4) * px(9), ey - Math.sin(a + 0.4) * px(9))
  ctx.closePath()
  ctx.fill()
  ctx.beginPath()
  ctx.arc(sx, sy, px(6), 0, Math.PI * 2)
  ctx.lineWidth = px(2)
  ctx.stroke()
  if (drag) {
    ctx.font = `${px(11)}px ui-monospace, monospace`
    ctx.textAlign = 'left'
    ctx.fillText(
      `${x0.toFixed(0)} km, ${y0.toFixed(0)} km · v ${Math.hypot(vx, vy).toFixed(2)} km/s`,
      sx + px(10),
      sy - px(10),
    )
  }
  ctx.restore()

  hits.push({
    x: sx,
    y: sy,
    r: 10,
    kind: 'handle',
    handle: 'start',
    color: SERIES[1],
    lines: ['Inbound object start', `${x0.toFixed(1)} km downrange · ${y0.toFixed(1)} km alt`, 'drag to move'],
  })
  hits.push({
    x: ex,
    y: ey,
    r: 10,
    kind: 'handle',
    handle: 'velocity',
    color: SERIES[1],
    lines: ['Inbound velocity', `${Math.hypot(vx, vy).toFixed(2)} km/s`, 'drag to change heading and speed'],
  })
}

// Bottom-left, in screen space, clear of the axis labels and the toolbar.
function drawLegend(ctx, h, layers) {
  const items = [
    ['predicted', 'rgba(195,194,183,0.6)', layers.corridor, 'dash'],
    ['inbound', SERIES[1], true, 'line'],
    ['truth', SERIES[0], layers.truth, 'line'],
    ['radar', SERIES[2], layers.radar, 'dot'],
    ['reported nav', SERIES[3], layers.reported, 'dash'],
  ].filter((item) => item[2])
  ctx.font = '10px ui-monospace, monospace'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'alphabetic'
  items.forEach(([label, color, , kind], i) => {
    const y = h - 56 - (items.length - 1 - i) * 14
    ctx.strokeStyle = color
    ctx.fillStyle = color
    ctx.lineWidth = 2
    if (kind === 'dot') ctx.fillRect(15, y - 5, 4, 4)
    else {
      ctx.setLineDash(kind === 'dash' ? [4, 3] : [])
      ctx.beginPath()
      ctx.moveTo(10, y - 3)
      ctx.lineTo(24, y - 3)
      ctx.stroke()
      ctx.setLineDash([])
    }
    ctx.fillStyle = INK.muted
    ctx.fillText(label, 30, y)
  })
}

function sampleLines(s, labels) {
  if (!s) return []
  const alerts = Object.entries(s.checks ?? {})
    .filter(([, level]) => LEVEL_RANK[level] >= 2)
    .map(([key, level]) => `  ${level} ${labels?.[key] ?? key}`)
  return [
    `T+${s.t.toFixed(1)} s · ${worstLevel(s.checks)}`,
    `radar ${s.radar[0].toFixed(1)} km, ${s.radar[1].toFixed(1)} km alt`,
    `speed ${s.radar_speed.toFixed(2)} km/s · nav error ${dist(s.reported, s.radar).toFixed(2)} km`,
    `predicted miss ${s.zem != null ? s.zem.toFixed(2) : '—'} km · tgo ${s.tgo != null ? s.tgo.toFixed(1) : '—'} s`,
    ...(alerts.length ? ['alerts:', ...alerts] : ['all checks nominal']),
    'click to seek',
  ]
}

function polyline(ctx, points) {
  if (points.length < 2) return
  ctx.beginPath()
  points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
  ctx.stroke()
}

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
const round = (v, digits) => Number(v.toFixed(digits))
