import { chromium } from 'playwright'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const file = process.argv[2] ?? '_preview.html'
const out = process.argv[3] ?? path.resolve(process.env.TEMP, 'preview.png')
const width = Number(process.argv[4] ?? 1240)
const height = Number(process.argv[5] ?? 430)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 })
await page.goto(pathToFileURL(path.resolve(file)).href)
await page.waitForTimeout(400)
await page.screenshot({ path: out })
await browser.close()
console.log('wrote', out)
