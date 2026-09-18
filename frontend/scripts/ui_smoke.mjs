// Browser smoke test for the mission-control dashboard.
//
// A production build compiling proves nothing about a canvas that throws on its
// first frame. This drives the real UI against a running backend: launch the
// mission, watch the ascent render, wait for orbit, inject a fault, and fail on
// any console error or page exception along the way. Screenshots land in
// `data/screenshots/` for the demo write-up.
//
//   node frontend/scripts/ui_smoke.mjs [--url http://localhost:5173] [--headed]
//
// Requires the backend (uvicorn) and the Vite dev server to be running.

import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

const url = argValue('--url') ?? 'http://localhost:5173'
const shotDir = path.resolve(process.cwd(), 'data/screenshots')
const problems = []

function argValue(flag) {
  const index = process.argv.indexOf(flag)
  return index === -1 ? undefined : process.argv[index + 1]
}

async function shoot(page, name) {
  await mkdir(shotDir, { recursive: true })
  await page.screenshot({ path: path.join(shotDir, `${name}.png`), fullPage: false })
}

// Every route is account-gated; the page's own session token authorises these calls.
function authed(page, path, method = 'GET') {
  return page.evaluate(
    async ([p, m]) => {
      const token = localStorage.getItem('astrix.session')
      const response = await fetch(p, { method: m, headers: { Authorization: `Bearer ${token}` } })
      return response.json()
    },
    [path, method],
  )
}

async function phase(page) {
  return (await authed(page, '/mission/status')).phase
}

async function waitForPhase(page, wanted, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const current = await phase(page)
    if (current === wanted) return current
    if (current === 'FAILED') throw new Error('mission runner failed')
    await page.waitForTimeout(250)
  }
  throw new Error(`phase ${wanted} not reached (last: ${await phase(page)})`)
}

const browser = await chromium.launch({ headless: !process.argv.includes('--headed') })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })

page.on('console', (message) => {
  // The sign-in gate answers 401 before the test's session exists.
  if (message.type() === 'error' && !/401/.test(message.text())) problems.push(`console: ${message.text()}`)
})
page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))

try {
  // The results notice is acknowledged once per session; pre-acknowledge it so
  // the dialog does not cover the controls this script clicks.
  await page.goto(`${url}/app#/assurance`, { waitUntil: 'networkidle' })
  await page.evaluate(async () => {
    sessionStorage.setItem('astrix.noticeAcknowledged.v1', '1')
    const response = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: `ui-smoke-${Date.now()}@example.com`, password: 'orbit-Transfer-42' }),
    })
    localStorage.setItem('astrix.session', (await response.json()).token)
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByRole('heading', { name: 'Overview' }).waitFor()
  // A stopped mission must still render the view rather than a blank panel.
  await page.locator('canvas.mission-canvas').waitFor()
  await shoot(page, '01-idle')

  await page.getByRole('button', { name: 'Launch mission' }).click()
  await page.getByText('Liftoff', { exact: false }).first().waitFor({ timeout: 20000 })
  await page.waitForTimeout(2500)
  await shoot(page, '02-ascent')

  // Fault injection must stay locked until the satellite exists.
  const injectDuringAscent = await page.getByRole('button', { name: 'Inject' }).isDisabled()
  if (!injectDuringAscent) problems.push('Inject button was enabled during ascent')

  await waitForPhase(page, 'ORBIT', 90000)
  await page.waitForTimeout(1500)
  await shoot(page, '03-orbit')

  await page.getByRole('button', { name: 'Inject' }).click()
  await page.getByText('INJECTED', { exact: false }).first().waitFor({ timeout: 15000 })
  await page.waitForTimeout(3000)
  await shoot(page, '04-fault-injected')

  const timelineVisible = await page.locator('.timeline-steps').count()
  if (!timelineVisible) problems.push('fault timeline did not render after injection')
} catch (error) {
  problems.push(`flow: ${error.message}`)
  await shoot(page, '99-failure').catch(() => {})
} finally {
  await authed(page, '/mission/stop', 'POST').catch(() => {})
  await browser.close()
}

if (problems.length) {
  console.error('UI smoke test FAILED:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`UI smoke test passed; screenshots in ${shotDir}`)
