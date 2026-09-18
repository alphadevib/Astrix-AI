// The standing "results are hypothetical" notice.
//
// Shown on every console page (sticky footer), in full on first visit, inside
// result panels, and on the landing page. It is not dismissible: it may be
// acknowledged, but it never disappears.

import { useState } from 'react'
import Icon from './icons'

export const NOTICE_SHORT =
  'Astrix results are hypothetical and must be verified using real-time prototypes and uploaded data.'

export const NOTICE_FULL =
  'Results produced by Astrix are hypothetical. Telemetry, launch, intercept and vehicle models are simulations and first-order estimates, and AI reasoning is advisory. Every result must be verified against real-time prototypes, hardware-in-the-loop tests and uploaded flight or test data before it informs any engineering or operational decision.'

const ACK_KEY = 'astrix.noticeAcknowledged.v1'

function acknowledged() {
  try {
    return window.sessionStorage.getItem(ACK_KEY) === '1'
  } catch {
    return false
  }
}

export function NoticeBar() {
  const [open, setOpen] = useState(() => !acknowledged())
  return (
    <>
      <div className="notice-bar" role="note">
        <Icon name="warning" size={14} />
        <span>{NOTICE_SHORT}</span>
        <button type="button" onClick={() => setOpen(true)}>
          Details
        </button>
      </div>
      {open && <NoticeDialog onClose={() => setOpen(false)} />}
    </>
  )
}

export function NoticeDialog({ onClose }) {
  const accept = () => {
    try {
      window.sessionStorage.setItem(ACK_KEY, '1')
    } catch {
      /* storage unavailable — the dialog will show again next load */
    }
    onClose()
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={accept}>
      <div
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="notice-title"
        aria-describedby="notice-body"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-icon warning">
          <Icon name="warning" size={22} />
        </div>
        <h2 id="notice-title">Results are hypothetical</h2>
        <p id="notice-body">{NOTICE_FULL}</p>
        <ul className="modal-list">
          <li>Simulated telemetry and physics are simplified models, not flight-qualified tools.</li>
          <li>LLM and Astrix-LM outputs are advisory and can be wrong.</li>
          <li>Confirm findings on hardware prototypes and with recorded test data.</li>
        </ul>
        <div className="modal-actions">
          <button type="button" className="btn primary" onClick={accept} autoFocus>
            I understand
          </button>
        </div>
      </div>
    </div>
  )
}

export function InlineNotice({ children }) {
  return (
    <p className="inline-notice" role="note">
      <Icon name="warning" size={14} />
      <span>{children ?? NOTICE_SHORT}</span>
    </p>
  )
}
