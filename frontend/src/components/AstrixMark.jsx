// The Astrix mark.
//
// A solid delta — an "A", a rocket's nose and a heading marker at once — with a
// four-point star cut out of its heart: the "aster" in Astrix. One silhouette,
// no strokes, so it stays a recognisable shape down to a 16px favicon, and the
// star reads as soon as there is room for it.
//
//   <AstrixMark />                 sized by CSS, fills with currentColor
//   <AstrixMark size={32} />       fixed size
//   <AstrixMark tone="nebula" />   the brand gradient
//
// The same geometry is written out in public/astrix-mark.svg and favicon.svg;
// change them together.

import { useId } from 'react'

// The delta: curved flanks, rounded tips, and a notch at the base that makes
// the two legs of the "A".
const DELTA =
  'M48 8C56 8 88 78 88 84C88 88 84 90 80 88L48 70L16 88C12 90 8 88 8 84C8 78 40 8 48 8Z'

// A four-point star with concave sides, centred in the delta's body.
const STAR = 'M48 32Q50.6 44.4 63 47Q50.6 49.6 48 62Q45.4 49.6 33 47Q45.4 44.4 48 32Z'

export default function AstrixMark({ size, tone = 'mono', className = '', title, ...rest }) {
  const uid = useId().replace(/:/g, '')
  const mask = `astrix-star-${uid}`
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
        <mask id={mask}>
          <rect width="96" height="96" fill="#fff" />
          <path d={STAR} fill="#000" />
        </mask>
        {tone === 'nebula' && (
          <linearGradient id={gradient} x1="20%" y1="0%" x2="80%" y2="100%">
            <stop offset="0%" stopColor="var(--brand-3, #A99BFF)" />
            <stop offset="55%" stopColor="var(--brand-2, #6150FF)" />
            <stop offset="100%" stopColor="var(--brand-1, #4F39F6)" />
          </linearGradient>
        )}
      </defs>
      <path d={DELTA} fill={fill} mask={`url(#${mask})`} />
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
