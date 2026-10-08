import { expect, test, type Page } from '@playwright/test'

const shots = process.env.SHOTS_DIR

async function visit(page: Page, path: string, name: string, text: RegExp) {
  const errors: string[] = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(path)
  await expect(page.getByText(text).first()).toBeVisible()
  if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true })
  expect(errors).toEqual([])
}

test('core pages render without errors', async ({ page, request }) => {
  // Start two offline runs through the UI's API and wait for them.
  const started = await (
    await request.post('/api/runs', { data: { benchmark: 'toy-tools', models: ['mock/strong', 'mock/flaky'] } })
  ).json()
  const ids: string[] = started.map((r: { id: string }) => r.id)
  for (const id of ids) {
    await expect
      .poll(async () => (await (await request.get(`/api/runs/${id}`)).json()).status, { timeout: 90_000 })
      .toBe('succeeded')
  }
  await visit(page, '/', 'runs', /Runs/)
  await visit(page, '/new', 'new-run', /Get quote/)
  await visit(page, `/runs/${ids[1]}`, 'run', /Samples/)
  const samples = await (await request.get(`/api/runs/${ids[1]}/samples?limit=1`)).json()
  await visit(page, `/traces/${samples.items[0].trace_id}`, 'trace', /Spans/)
  await visit(page, `/compare?a=${ids[0]}&b=${ids[1]}`, 'compare', /McNemar/)
  const cmp = await (await request.get(`/api/compare?a=${ids[0]}&b=${ids[1]}`)).json()
  const changed = cmp.rows.find((r: { change: string }) => r.change === 'regression' || r.change === 'improvement')
  await visit(page, `/diff?a=${changed.a_trace_id}&b=${changed.b_trace_id}`, 'diff', /First divergence/)
  await visit(page, '/catalog', 'catalog', /gsm8k@1/)

  const traceId = [...crypto.getRandomValues(new Uint8Array(16))].map((x) => x.toString(16).padStart(2, '0')).join('')
  await request.post('/v1/traces', {
    headers: { 'Content-Type': 'application/json', 'x-everyeval-source': 'sdk' },
    data: {
      resourceSpans: [
        {
          scopeSpans: [
            {
              spans: [
                {
                  traceId,
                  spanId: '0011223344556677',
                  name: 'my-agent-task',
                  startTimeUnixNano: '1700000000000000000',
                  endTimeUnixNano: '1700000002000000000',
                  attributes: [{ key: 'openinference.span.kind', value: { stringValue: 'AGENT' } }],
                },
              ],
            },
          ],
        },
      ],
    },
  })
  await visit(page, '/traces', 'traces', /my-agent-task/)
  await visit(page, `/traces/${traceId}`, 'sdk-trace', /my-agent-task/)
})

test('quote, approve and run from the UI', async ({ page }) => {
  await page.goto('/new')
  await page.getByRole('button', { name: 'Get quote' }).click()
  await expect(page.getByText('Upper estimate')).toBeVisible({ timeout: 60_000 })
  await page.locator('#cap').fill('1')
  await page.getByRole('button', { name: 'Approve and run' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByText('mock/weak').first()).toBeVisible()
})

test('draft, review and approve dataset items from run failures', async ({ page, request }) => {
  const [run] = await (
    await request.post('/api/runs', { data: { benchmark: 'toy-arith', models: ['mock/weak'], limit: 12 } })
  ).json()
  await expect
    .poll(async () => (await (await request.get(`/api/runs/${run.id}`)).json()).status, { timeout: 60_000 })
    .toBe('succeeded')
  await page.goto(`/runs/${run.id}`)
  await page.getByRole('button', { name: 'Add failures to dataset' }).click()
  await page.locator('#dataset-name').fill(`misses ${run.id}`)
  await page.getByRole('button', { name: 'Add drafts' }).click()
  await expect(page.getByText(/draft item/)).toBeVisible()

  await page.goto('/datasets')
  await page.getByRole('link', { name: `misses ${run.id}` }).click()
  await page
    .getByRole('button', { name: /Use benchmark target/ })
    .first()
    .click()
  await page.getByRole('button', { name: 'Approve' }).first().click()
  await expect(page.getByText(/Cannot approve: no split/)).toBeVisible()
  await page.getByRole('combobox', { name: 'Split' }).first().click()
  await page.getByRole('option', { name: 'Held-out test' }).click()
  await page.getByRole('button', { name: 'Approve' }).first().click()
  await expect(page.getByText(/: approved/)).toBeVisible()
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/dataset.png`, fullPage: true })
  await expect(page.getByRole('link', { name: 'Export JSONL' })).toBeVisible()
})
