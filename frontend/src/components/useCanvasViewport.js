// Pan, zoom and pointer tracking for a 2D canvas scene.
//
// The scene draws in its own "base" screen coordinates (as if unzoomed); the
// viewport is a transform applied on top: `screen = base * scale + offset`.
// Wheel zooms around the cursor, drag pans, double-click resets. Pointer state
// is kept in refs, not React state, because the canvas redraws every frame
// anyway and re-rendering React on every mouse move would be waste.
//
// A scene can claim a drag (e.g. dragging a handle) by returning true from
// `onDragStart`; the viewport then forwards moves to `onDrag` instead of panning.

import { useCallback, useEffect, useRef } from 'react'

const MIN_SCALE = 0.5
const MAX_SCALE = 40

export default function useCanvasViewport(canvasRef, { onDragStart, onDrag, onDragEnd, onClick } = {}) {
  const view = useRef({ scale: 1, ox: 0, oy: 0 })
  const pointer = useRef({ x: 0, y: 0, inside: false, down: false, moved: false, mode: null, lastX: 0, lastY: 0 })
  const handlers = useRef({ onDragStart, onDrag, onDragEnd, onClick })
  handlers.current = { onDragStart, onDrag, onDragEnd, onClick }

  const toBase = useCallback((x, y) => {
    const { scale, ox, oy } = view.current
    return [(x - ox) / scale, (y - oy) / scale]
  }, [])

  const zoomAt = useCallback((factor, x, y) => {
    const v = view.current
    const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, v.scale * factor))
    const k = next / v.scale
    view.current = { scale: next, ox: x - (x - v.ox) * k, oy: y - (y - v.oy) * k }
  }, [])

  const zoomBy = useCallback(
    (factor) => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      zoomAt(factor, rect.width / 2, rect.height / 2)
    },
    [canvasRef, zoomAt],
  )

  const reset = useCallback(() => {
    view.current = { scale: 1, ox: 0, oy: 0 }
  }, [])

  // Centre the view on a base-coordinate point (used by "follow").
  const centerOn = useCallback(
    (bx, by, w, h) => {
      const { scale } = view.current
      view.current = { scale, ox: w / 2 - bx * scale, oy: h / 2 - by * scale }
    },
    [],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const local = (event) => {
      const rect = canvas.getBoundingClientRect()
      return [event.clientX - rect.left, event.clientY - rect.top]
    }

    const wheel = (event) => {
      event.preventDefault()
      const [x, y] = local(event)
      zoomAt(Math.exp(-event.deltaY * 0.0015), x, y)
    }
    const down = (event) => {
      if (event.button !== 0) return
      const [x, y] = local(event)
      const p = pointer.current
      p.down = true
      p.moved = false
      p.lastX = x
      p.lastY = y
      p.mode = handlers.current.onDragStart?.(...toBase(x, y), x, y) ? 'handle' : 'pan'
      canvas.setPointerCapture(event.pointerId)
    }
    const move = (event) => {
      const [x, y] = local(event)
      const p = pointer.current
      p.x = x
      p.y = y
      p.inside = true
      if (!p.down) return
      const dx = x - p.lastX
      const dy = y - p.lastY
      // Until the pointer travels a few pixels this is still a click, not a drag.
      if (!p.moved && Math.abs(dx) + Math.abs(dy) <= 3) return
      p.moved = true
      if (p.mode === 'handle') handlers.current.onDrag?.(...toBase(x, y), x, y)
      else view.current = { ...view.current, ox: view.current.ox + dx, oy: view.current.oy + dy }
      p.lastX = x
      p.lastY = y
    }
    const up = (event) => {
      const p = pointer.current
      if (!p.down) return
      const [x, y] = local(event)
      if (p.mode === 'handle') handlers.current.onDragEnd?.(...toBase(x, y), x, y)
      else if (!p.moved) handlers.current.onClick?.(...toBase(x, y), x, y)
      p.down = false
      p.mode = null
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId)
    }
    const leave = () => {
      pointer.current.inside = false
    }
    const dbl = () => reset()

    canvas.addEventListener('wheel', wheel, { passive: false })
    canvas.addEventListener('pointerdown', down)
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerup', up)
    canvas.addEventListener('pointercancel', up)
    canvas.addEventListener('pointerleave', leave)
    canvas.addEventListener('dblclick', dbl)
    return () => {
      canvas.removeEventListener('wheel', wheel)
      canvas.removeEventListener('pointerdown', down)
      canvas.removeEventListener('pointermove', move)
      canvas.removeEventListener('pointerup', up)
      canvas.removeEventListener('pointercancel', up)
      canvas.removeEventListener('pointerleave', leave)
      canvas.removeEventListener('dblclick', dbl)
    }
  }, [canvasRef, reset, toBase, zoomAt])

  // Applies the viewport on top of the device-pixel-ratio transform.
  const apply = useCallback((ctx, dpr) => {
    const { scale, ox, oy } = view.current
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * ox, dpr * oy)
  }, [])

  return { view, pointer, toBase, zoomBy, reset, centerOn, apply }
}

// Finds the hit target nearest the pointer (in base coordinates) within its radius.
export function pickHit(hits, bx, by, scale) {
  let best = null
  let bestD = Infinity
  for (const hit of hits) {
    const d = Math.hypot(hit.x - bx, hit.y - by)
    const reach = (hit.r ?? 10) / Math.min(scale, 3) + 4 / scale
    if (d <= reach && d < bestD) {
      best = hit
      bestD = d
    }
  }
  return best
}

// Tooltip box in screen space, kept inside the canvas.
export function drawTooltip(ctx, x, y, w, h, lines, accent) {
  if (!lines?.length) return
  ctx.save()
  ctx.font = '11px ui-monospace, monospace'
  const pad = 7
  const lineH = 15
  const width = Math.max(...lines.map((l) => ctx.measureText(l).width)) + pad * 2
  const height = lines.length * lineH + pad * 2 - 3
  let tx = x + 14
  let ty = y + 14
  if (tx + width > w - 4) tx = x - width - 14
  if (ty + height > h - 4) ty = y - height - 14
  tx = Math.max(4, tx)
  ty = Math.max(4, ty)
  ctx.fillStyle = 'rgba(24, 24, 22, 0.94)'
  ctx.strokeStyle = accent ?? '#4a4a42'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.roundRect(tx, ty, width, height, 5)
  ctx.fill()
  ctx.stroke()
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  lines.forEach((line, i) => {
    ctx.fillStyle = i === 0 ? '#ffffff' : '#c3c2b7'
    ctx.fillText(line, tx + pad, ty + pad + i * lineH)
  })
  ctx.restore()
}
