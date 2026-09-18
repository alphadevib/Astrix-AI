// Astrix's presence: a small planet.
//
// The reference design gives its agent a glowing sphere rather than an avatar,
// and for a spacecraft console that reads as exactly the right object — a body
// with a limb, a terminator and an atmosphere, lit from one side the way
// anything in orbit is. It is the one place in the console where the visual
// language is allowed to be atmospheric; everything around it stays flat.
//
// Pure CSS and SVG, no canvas and no animation frame: the orb idles on two slow
// keyframes and stops entirely under `prefers-reduced-motion`. `state` drives
// what it is doing, which is how the operator can tell at a glance whether
// Astrix is idle, working or reporting a fault.

import { useId } from 'react'

const STATE_CLASS = {
  idle: 'is-idle',
  thinking: 'is-thinking',
  alert: 'is-alert',
}

export default function AstrixOrb({ size = 88, state = 'idle', className = '' }) {
  const uid = useId().replace(/:/g, '')
  const body = `orb-body-${uid}`
  const sheen = `orb-sheen-${uid}`
  const atmosphere = `orb-atmosphere-${uid}`
  const clip = `orb-clip-${uid}`

  return (
    <span
      className={`astrix-orb ${STATE_CLASS[state] ?? STATE_CLASS.idle} ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <span className="orb-halo" />
      <svg viewBox="0 0 120 120" width={size} height={size}>
        <defs>
          {/* Lit from the upper left, as the console's own light falls. */}
          <radialGradient id={body} cx="34%" cy="28%" r="78%">
            <stop offset="0%" stopColor="#C9C1FF" />
            <stop offset="34%" stopColor="#6150FF" />
            <stop offset="68%" stopColor="#3421C9" />
            <stop offset="100%" stopColor="#0B0724" />
          </radialGradient>
          <radialGradient id={sheen} cx="30%" cy="22%" r="42%">
            <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.85" />
            <stop offset="100%" stopColor="#FFFFFF" stopOpacity="0" />
          </radialGradient>
          <radialGradient id={atmosphere} cx="50%" cy="50%" r="50%">
            <stop offset="72%" stopColor="#8B7BFF" stopOpacity="0" />
            <stop offset="93%" stopColor="#8B7BFF" stopOpacity="0.42" />
            <stop offset="100%" stopColor="#8B7BFF" stopOpacity="0" />
          </radialGradient>
          <clipPath id={clip}>
            <circle cx="60" cy="60" r="42" />
          </clipPath>
        </defs>

        <circle cx="60" cy="60" r="52" fill={`url(#${atmosphere})`} />
        <circle cx="60" cy="60" r="42" fill={`url(#${body})`} />

        {/* Cloud bands, clipped to the sphere so they curve with the limb. */}
        <g clipPath={`url(#${clip})`} className="orb-bands">
          <ellipse cx="46" cy="40" rx="40" ry="9" fill="#FFFFFF" opacity="0.13" />
          <ellipse cx="70" cy="62" rx="46" ry="7" fill="#FFFFFF" opacity="0.09" />
          <ellipse cx="52" cy="82" rx="38" ry="6" fill="#0B0918" opacity="0.22" />
        </g>

        <circle cx="60" cy="60" r="42" fill={`url(#${sheen})`} />
        <circle cx="60" cy="60" r="42" fill="none" stroke="#FFFFFF" strokeOpacity="0.16" strokeWidth="1" />

        {/* A satellite on a tilted orbit — the console's whole subject, in one dot. */}
        <g className="orb-orbit">
          <ellipse
            cx="60"
            cy="60"
            rx="56"
            ry="20"
            fill="none"
            stroke="#FFFFFF"
            strokeOpacity="0.2"
            strokeWidth="1"
            transform="rotate(-24 60 60)"
          />
          <circle className="orb-satellite" cx="111" cy="38" r="3.4" fill="#8B7BFF" />
        </g>
      </svg>
    </span>
  )
}
