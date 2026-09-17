// The Astrix mark.
//
// A four-point star crossed by an orbit. The name carries "asterisk", so the
// mark is one — drawn as four tapered blades rather than strokes, with an
// elliptical orbit passing behind it. The orbit is knocked out where the star
// crosses, which is what gives the ring its depth in a single flat colour.
//
// Everything is one closed path plus one stroked ellipse, so the mark fills with
// `currentColor`, survives down to a 14px favicon, and needs no raster fallback.
//
//   <AstrixMark />                 sized by CSS, inherits colour
//   <AstrixMark size={32} />       fixed size
//   <AstrixMark tone="nebula" />   the brand gradient
//
// `tone="nebula"` needs a unique gradient id per instance, so ids are derived
// from React's useId — two marks on one page must not share a <defs>.

import { useId } from 'react'

// Generated from the geometry in scripts/brand.py; edit there, not here.
const STAR =
  'M48.0 7.0C48.95 19.3 58.5 35.7 53.5 45.54L48.0 48.0L42.5 45.54C40.8 35.7 47.61 19.3 48.0 7.0Z' +
  'M89.0 48.0C76.7 48.95 60.3 58.5 50.46 53.5L48.0 48.0L50.46 42.5C60.3 40.8 76.7 47.61 89.0 48.0Z' +
  'M48.0 89.0C47.05 76.7 37.5 60.3 42.5 50.46L48.0 48.0L53.5 50.46C55.2 60.3 48.39 76.7 48.0 89.0Z' +
  'M7.0 48.0C19.3 47.06 35.7 37.5 45.54 42.5L48.0 48.0L45.54 53.5C35.7 55.2 19.3 48.39 7.0 48.0Z'

const RING = { rx: 43, ry: 16.5, rot: -22, width: 7.5, gap: 5.5 }

export default function AstrixMark({ size, tone = 'mono', className = '', title, ...rest }) {
  const uid = useId().replace(/:/g, '')
  const mask = `astrix-orbit-${uid}`
  const gradient = `astrix-nebula-${uid}`
  const fill = tone === 'nebula' ? `url(#${gradient})` : 'currentColor'
  const dimensions = size ? { width: size, height: size } : undefined

  return (
    <svg
      viewBox="0 0 96 96"
      className={`astrix-mark ${className}`}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      {...dimensions}
      {...rest}
    >
      {title && <title>{title}</title>}
      <defs>
        {/* The orbit is hidden wherever the star crosses it — plus a stroke's
            worth of clearance either side, which is what reads as "in front". */}
        <mask id={mask}>
          <rect width="96" height="96" fill="#fff" />
          <path d={STAR} fill="#000" stroke="#000" strokeWidth={RING.gap} strokeLinejoin="round" />
        </mask>
        {tone === 'nebula' && (
          <linearGradient id={gradient} x1="6%" y1="0%" x2="94%" y2="100%">
            <stop offset="0%" stopColor="var(--brand-1, #8B7BFF)" />
            <stop offset="52%" stopColor="var(--brand-2, #A78BFA)" />
            <stop offset="100%" stopColor="var(--brand-3, #4FD8E4)" />
          </linearGradient>
        )}
      </defs>
      <g fill={fill}>
        <g mask={`url(#${mask})`}>
          <ellipse
            cx="48"
            cy="48"
            rx={RING.rx}
            ry={RING.ry}
            fill="none"
            stroke={fill}
            strokeWidth={RING.width}
            transform={`rotate(${RING.rot} 48 48)`}
          />
        </g>
        <path d={STAR} />
      </g>
    </svg>
  )
}

// Mark plus wordmark, for the sidebar and the landing header. The name is set in
// the UI font rather than outlined, so it stays crisp at any size and inherits
// the page's own type rendering.
export function AstrixWordmark({ size = 22, tone = 'nebula', className = '' }) {
  return (
    <span className={`astrix-wordmark ${className}`}>
      <AstrixMark size={size} tone={tone} />
      <span className="astrix-wordmark-text">Astrix</span>
    </span>
  )
}
