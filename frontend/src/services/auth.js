// The operator's session: a bearer token from /auth/login or /auth/register.
//
// The token is the console's only credential. It lives in localStorage so a
// reload keeps the operator signed in; the server stores only its hash and can
// revoke it at any time, at which point the next request answers 401 and every
// listener here is told to show the sign-in page again.

const SESSION_KEY = 'astrix.session'

const listeners = new Set()

function read() {
  try {
    return window.localStorage.getItem(SESSION_KEY) || ''
  } catch {
    return ''
  }
}

function write(token) {
  try {
    if (token) window.localStorage.setItem(SESSION_KEY, token)
    else window.localStorage.removeItem(SESSION_KEY)
  } catch {
    /* storage unavailable (private mode) — the session lasts until reload */
  }
}

let current = read()

export const session = {
  token: () => current,
  set(token) {
    current = token || ''
    write(current)
    listeners.forEach((fn) => fn(current))
  },
  // Called by the API client on any 401: the session was revoked or expired.
  expire() {
    if (!current) return
    session.set('')
  },
  subscribe(fn) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
}

// A previous build stored a pasted machine token here; it is not a session.
try {
  window.localStorage.removeItem('astrix.apiToken')
} catch {
  /* storage unavailable */
}

export default session
