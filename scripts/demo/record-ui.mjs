// Records a walkthrough of the web UI as video, and takes gallery screenshots.
// Called by record.sh, which seeds the data and passes the ids:
//   node scripts/demo/record-ui.mjs OUT_DIR RUN_A RUN_B DATASET_ID
import { mkdirSync, readdirSync, renameSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join, resolve } from 'node:path'

const require = createRequire(resolve('web/package.json'))
const { chromium } = require('playwright')

const [out, runA, runB, dataset] = process.argv.slice(2)
const base = process.env.EVERYEVAL_URL ?? 'http://127.0.0.1:8321'
const size = { width: 1270, height: 760 } // Product Hunt gallery size
const api = async (path) => (await fetch(base + path)).json()

// Playwright videos do not show the pointer, so draw one that follows the mouse.
const cursor = () => {
  window.addEventListener('DOMContentLoaded', () => {
    const dot = document.createElement('div')
    dot.style.cssText =
      'position:fixed;z-index:99999;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;' +
      'background:rgba(37,99,235,.35);border:2px solid rgba(37,99,235,.9);pointer-events:none;' +
      'transition:transform .12s;left:-40px;top:-40px'
    document.body.append(dot)
    addEventListener('mousemove', (e) => ((dot.style.left = e.clientX + 'px'), (dot.style.top = e.clientY + 'px')))
    addEventListener('mousedown', () => (dot.style.transform = 'scale(.7)'))
    addEventListener('mouseup', () => (dot.style.transform = ''))
  })
}

const cmp = await api(`/api/compare?a=${runA}&b=${runB}`)
const changed = cmp.rows.find((r) => r.change === 'regression')
// The successful SDK trace (q-1, 4 spans). The other one errors on purpose and its stack trace shows local paths.
const sdkTrace = (await api('/api/traces?source=sdk')).find((t) => t.name === 'solve-question' && t.span_count === 4)

const browser = await chromium.launch()

// Screenshots: 2x for sharpness, cropped to the viewport so every image has the same aspect.
{
  const page = await browser.newPage({ viewport: size, deviceScaleFactor: 2 })
  const shot = async (path, name, ready, after) => {
    await page.goto(base + path)
    await page.getByText(ready).first().waitFor()
    if (after) await after()
    await page.waitForTimeout(400)
    await page.screenshot({ path: join(out, `ui-${name}.png`) })
  }
  await shot('/', '1-runs', /Runs/)
  await shot(`/compare?a=${runA}&b=${runB}`, '2-compare', /McNemar/)
  await shot(`/diff?a=${changed.a_trace_id}&b=${changed.b_trace_id}`, '3-trace-diff', /First divergence/)
  await shot(`/traces/${changed.b_trace_id}`, '4-trace', /Spans/, () =>
    page.getByText('calculator', { exact: true }).first().click(),
  )
  await shot(`/traces/${sdkTrace.trace_id}`, '5-sdk-trace', /solve-question/)
  await shot('/new', '6-new-run-quote', /Get quote/)
  await shot(`/datasets/${dataset}`, '7-dataset', /calculator failures/)
  await shot('/catalog', '8-catalog', /gsm8k@1/)
  await page.close()
}

// Video: one continuous walkthrough.
{
  const videoDir = join(out, '.video')
  const context = await browser.newContext({ viewport: size, recordVideo: { dir: videoDir, size } })
  await context.addInitScript(cursor)
  const page = await context.newPage()
  const pause = (ms) => page.waitForTimeout(ms)
  const click = async (locator) => {
    await locator.scrollIntoViewIfNeeded()
    const box = await locator.boundingBox()
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 })
    await pause(350)
    await locator.click()
  }
  const scroll = async (dy) => {
    for (let i = 0; i < 20; i++) (await page.mouse.wheel(0, dy / 20), await pause(25))
  }

  await page.goto(base + '/')
  await page.getByText(runA).first().waitFor()
  await page.mouse.move(640, 300)
  await pause(1500)

  // Pick the two toy-tools runs and compare them.
  for (const id of [runA, runB]) await click(page.getByRole('row').filter({ hasText: id }).getByRole('checkbox'))
  await pause(400)
  await click(page.getByRole('button', { name: /Compare selected/ }).or(page.getByRole('link', { name: /Compare selected/ })))
  await page.getByText(/McNemar/).first().waitFor()
  await pause(2500)
  await scroll(500)
  await pause(1500)

  // Open the first regression's trace diff.
  await click(page.getByRole('row').filter({ hasText: changed.sample_id }).getByRole('link', { name: 'Diff' }))
  await page.getByText(/First divergence/).first().waitFor()
  await pause(3000)
  await scroll(300)
  await pause(1500)

  // Then the failing trace itself, and its tool call.
  await page.goto(`${base}/traces/${changed.b_trace_id}`)
  await page.getByText(/Spans/).first().waitFor()
  await pause(1200)
  await click(page.getByText('calculator', { exact: true }).first())
  await pause(2500)

  // A trace sent from your own agent with the SDK.
  await click(page.getByRole('link', { name: 'Traces', exact: true }))
  const sdkRow = page.getByRole('row').filter({ hasText: sdkTrace.trace_id.slice(0, 16) })
  await sdkRow.waitFor()
  await pause(1200)
  await click(sdkRow.getByRole('link'))
  await pause(2500)

  // Failures become dataset drafts for review.
  await click(page.getByRole('link', { name: 'Datasets', exact: true }))
  await pause(800)
  await click(page.getByText('calculator failures').first())
  await pause(3000)

  await context.close()
  const [video] = readdirSync(videoDir)
  renameSync(join(videoDir, video), join(out, 'ui-walkthrough.webm'))
  rmSync(videoDir, { recursive: true })
}

await browser.close()
