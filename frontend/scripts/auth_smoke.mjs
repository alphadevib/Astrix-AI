// Browser smoke test for accounts and conversation threads.
//
// Signs up a fresh account, holds a conversation, starts a new one, switches
// back, deletes a thread, opens the profile console and signs out — failing on
// any console error or page exception. Screenshots land in data/screenshots/.
//
//   node frontend/scripts/auth_smoke.mjs [--url http://localhost:5173] [--headed]
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

function expect(condition, message) {
  if (!condition) problems.push(message)
}

const browser = await chromium.launch({ headless: !process.argv.includes('--headed') })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

page.on('console', (message) => {
  // A 401 before sign-in is the gate working, not a fault.
  if (message.type() === 'error' && !/401/.test(message.text())) problems.push(`console: ${message.text()}`)
})
page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))

const email = `smoke-${Date.now()}@example.com`
const password = 'orbit-Transfer-42'
const threads = () => page.locator('.thread-row')

try {
  await page.goto(url, { waitUntil: 'networkidle' })
  await shoot(page, 'auth-00-landing')

  await page.goto(`${url}/app`, { waitUntil: 'networkidle' })
  await page.evaluate(() => sessionStorage.setItem('astrix.noticeAcknowledged.v1', '1'))
  await page.getByRole('heading', { name: 'Sign in to Astrix' }).waitFor()
  await shoot(page, 'auth-01-sign-in')

  // A wrong password must be refused without leaving the page.
  await page.getByLabel('Email').fill('nobody@example.com')
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('alert').waitFor()

  await page.getByRole('button', { name: 'Create an account' }).click()
  await page.getByLabel('Name').fill('Smoke Test')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await shoot(page, 'auth-02-sign-up')
  await page.getByRole('button', { name: 'Create account' }).click()

  await page.getByRole('heading', { name: 'What are we testing today?' }).waitFor({ timeout: 15000 })
  await page.locator('.live-dot.on').waitFor({ timeout: 10000 })
  await shoot(page, 'auth-03-empty-chat')

  // First conversation: the server creates the thread on the first message.
  await page.getByLabel('Message Astrix').fill('status')
  await page.keyboard.press('Enter')
  await page.locator('.msg.assistant').first().waitFor({ timeout: 20000 })
  await threads().first().waitFor({ timeout: 10000 })
  expect((await threads().count()) === 1, 'first message did not create exactly one thread')
  await shoot(page, 'auth-04-first-thread')

  // New conversation must be empty — the original bug.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await page.getByRole('heading', { name: 'What are we testing today?' }).waitFor()
  expect((await page.locator('.msg').count()) === 0, 'New conversation still showed old messages')

  await page.getByLabel('Message Astrix').fill('help')
  await page.keyboard.press('Enter')
  await page.locator('.msg.assistant').first().waitFor({ timeout: 20000 })
  await page.waitForFunction(() => document.querySelectorAll('.thread-row').length === 2, null, { timeout: 10000 })

  // Switching back loads the first thread's turns from the server.
  await threads().nth(1).click()
  await page.locator('.msg.user .msg-bubble', { hasText: 'status' }).waitFor({ timeout: 10000 })
  expect(
    (await page.locator('.msg.user .msg-bubble', { hasText: 'help' }).count()) === 0,
    'switching threads mixed messages from another thread',
  )

  // Reload keeps the session and the open thread.
  await page.reload({ waitUntil: 'networkidle' })
  await page.locator('.msg.user .msg-bubble', { hasText: 'status' }).waitFor({ timeout: 10000 })
  await shoot(page, 'auth-05-switched')

  // Delete the open thread: the chat resets and the list shrinks.
  page.once('dialog', (dialog) => dialog.accept())
  await page.locator('.thread-row.active .thread-delete').click({ force: true })
  await page.waitForFunction(() => document.querySelectorAll('.thread-row').length === 1, null, { timeout: 10000 })
  await page.getByRole('heading', { name: 'What are we testing today?' }).waitFor()

  // Profile console.
  await page.locator('.account-chip').click()
  await page.getByRole('menuitem', { name: 'Profile and settings' }).click()
  await page.getByRole('dialog').waitFor()
  await page.getByLabel('Organisation').fill('Smoke Labs')
  await page.getByRole('button', { name: 'Save profile' }).click()
  await page.getByText('Profile saved.').waitFor()
  await page.getByRole('tab', { name: 'Security' }).click()
  await page.getByText('This device').waitFor()
  await shoot(page, 'auth-06-profile')
  expect((await page.getByText('API token').count()) === 0, 'the API-key field is still present')
  await page.getByRole('button', { name: 'Close' }).click()
  expect((await page.locator('.account-role', { hasText: 'Smoke Labs' }).count()) === 1, 'profile edit did not reach the chip')

  // Every lab page renders behind the gate.
  for (const key of ['assurance', 'studio', 'hardware', 'model', 'memory']) {
    await page.goto(`${url}/app#/${key}`)
    await page.waitForTimeout(1200)
  }
  await page.goto(`${url}/app#/assistant`)

  // Sign out returns to the sign-in page, and the old token is dead server-side.
  const token = await page.evaluate(() => localStorage.getItem('astrix.session'))
  await page.locator('.account-chip').click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await page.getByRole('heading', { name: 'Sign in to Astrix' }).waitFor()
  const status = await page.evaluate(
    async (t) => (await fetch('/auth/me', { headers: { Authorization: `Bearer ${t}` } })).status,
    token,
  )
  expect(status === 401, `revoked token still accepted (status ${status})`)
} catch (error) {
  problems.push(`flow: ${error.message}`)
  await shoot(page, 'auth-99-failure').catch(() => {})
} finally {
  await browser.close()
}

if (problems.length) {
  console.error('Auth smoke test FAILED:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`Auth smoke test passed; screenshots in ${shotDir}`)
