import { expect, test } from '@playwright/test'
import type { DatasetItem, Run, Sample } from '../src/api'

// Historical model runs remain readable; these fixtures never schedule the removed solver execution path.
const run: Run = {
  id: 'historical-run',
  group_id: null,
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  finished_at: null,
  status: 'succeeded',
  benchmark: 'historical-benchmark',
  variant_key: 'historical-v1',
  model: 'legacy-model',
  target_kind: 'model',
  agent: null,
  resolved_models: ['legacy-model'],
  limit: 1,
  epochs: 1,
  budget_usd: null,
  content_policy: 'full',
  quote_id: null,
  samples_total: 1,
  samples_done: 1,
  n_correct: 0,
  n_error: 0,
  input_tokens: 10,
  output_tokens: 2,
  cost_usd: null,
  metrics: {},
  log_path: null,
  error: null,
  accuracy: 0,
  score: null,
  purpose: 'eval',
  outcomes: { incorrect: 1 },
}
const sample: Sample = {
  sample_id: 'case-1',
  epoch: 1,
  outcome: 'incorrect',
  score: 0,
  score_raw: null,
  scorer: 'historical',
  answer: '3',
  explanation: 'Expected 4',
  target: '4',
  input: '2+2',
  output: '3',
  error: null,
  input_tokens: 10,
  output_tokens: 2,
  total_time: 1,
  trace_id: 'historical-trace',
  run_id: run.id,
}
const header = {
  trace_id: 'historical-trace',
  run_id: run.id,
  sample_id: sample.sample_id,
  name: 'Historical task',
  source: 'inspect',
  source_ref: null,
  created_at: run.created_at,
  start_time: null,
  end_time: null,
  span_count: 1,
  state: 'complete',
  attributes: {},
}
const sdkHeader = {
  ...header,
  trace_id: 'sdk-trace',
  run_id: null,
  sample_id: null,
  name: 'my-agent-task',
  source: 'sdk',
}
const span = {
  trace_id: header.trace_id,
  span_id: 'root',
  parent_id: null,
  run_id: run.id,
  name: 'answer',
  kind: 'model',
  start_time: null,
  end_time: null,
  status: 'ok',
  attributes: {},
  content: { output: '3' },
  content_state: 'full',
  source: 'inspect',
}

const shots = process.env.SHOTS_DIR

test('historical run, samples, SDK and imported traces, comparison and trace diff render without errors', async ({
  page,
}) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const json =
      path === '/api/auth/me'
        ? { auth_enabled: false, user: 'local', role: 'owner', via: 'local', workspace: null, workspaces: [] }
        : path === '/api/runs'
          ? [run]
          : path.endsWith('/samples')
            ? { total: 1, items: [sample] }
            : path === `/api/runs/${run.id}`
              ? run
              : path === '/api/traces'
                ? [header, sdkHeader]
                : path === '/api/traces/sdk-trace'
                  ? { trace: sdkHeader, sample: null, spans: [{ ...span, trace_id: 'sdk-trace', source: 'sdk' }] }
                  : path === '/api/traces/historical-trace'
                    ? { trace: header, sample, spans: [span] }
                    : path === '/api/compare'
                      ? {
                          run_a: run,
                          run_b: run,
                          compatibility: { comparable: true, blocking: [], warnings: [] },
                          n_paired: 1,
                          n_scored_pairs: 1,
                          mean_a: 0,
                          mean_b: 1,
                          delta: 1,
                          delta_ci95: [0, 1],
                          mcnemar_p: 1,
                          discordant: { improvements: 1 },
                          errors: {},
                          missing: {},
                          rows: [
                            {
                              sample_id: 'case-1',
                              epoch: 1,
                              a_outcome: 'incorrect',
                              b_outcome: 'correct',
                              a_score: 0,
                              b_score: 1,
                              change: 'improvement',
                              a_trace_id: 'historical-trace',
                              b_trace_id: 'sdk-trace',
                            },
                          ],
                        }
                      : path === '/api/trace-diff'
                        ? {
                            a: { ...header, outcome: 'incorrect', score: 0 },
                            b: { ...sdkHeader, outcome: 'correct', score: 1 },
                            pairs: [
                              {
                                op: 'changed',
                                a: {
                                  span_id: 'root',
                                  kind: 'model',
                                  name: 'answer',
                                  status: 'ok',
                                  summary: '3',
                                  output: { answer: '3' },
                                },
                                b: {
                                  span_id: 'root',
                                  kind: 'model',
                                  name: 'answer',
                                  status: 'ok',
                                  summary: '4',
                                  output: { answer: '4' },
                                },
                                differences: ['output'],
                              },
                            ],
                            first_divergence: 0,
                            summary: 'Answer changed from 3 to 4.',
                          }
                        : null
    if (!json) throw new Error(`Unexpected API call: ${path}`)
    await route.fulfill({ json })
  })
  for (const [path, text] of [
    ['/', 'Runs'],
    [`/runs/${run.id}`, 'Samples (1)'],
    ['/traces/historical-trace', 'Historical task incorrect'],
    ['/traces', 'Traces'],
    ['/traces/sdk-trace', 'my-agent-task'],
    [`/compare?a=${run.id}&b=${run.id}`, 'McNemar p'],
    ['/diff?a=historical-trace&b=sdk-trace', 'First divergence'],
  ]) {
    await page.goto(path)
    await expect(page.getByText(text, { exact: true }).first()).toBeVisible()
    if (shots)
      await page.screenshot({
        path: `${shots}/${path.split('?')[0].replaceAll('/', '-') || 'runs'}.png`,
        fullPage: true,
      })
  }
  expect(errors).toEqual([])
})

test('draft, review and approve dataset items from historical run failures', async ({ page }) => {
  let created = false
  let item: DatasetItem = {
    id: 1,
    dataset_id: 'misses',
    source_trace_id: header.trace_id,
    source: 'inspect',
    input: '2+2',
    historical_output: '3',
    historical_score: 0,
    reference_output: null,
    split: 'unassigned',
    status: 'draft',
    usage_rights: 'own_data',
    provenance: { target: '4' },
    notes: null,
    reviewed_at: null,
    reviewed_by: null,
  }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const dataset = { id: 'misses', name: 'Historical misses', description: null, created_at: run.created_at }
    if (method === 'PATCH' && path === '/api/datasets/misses/items/1') {
      const body = route.request().postDataJSON()
      if (body.status === 'approved' && body.split === 'unassigned')
        return route.fulfill({ status: 400, json: { detail: 'Cannot approve: no split selected.' } })
      item = { ...item, ...body }
      return route.fulfill({ json: item })
    }
    if (method === 'POST' && path === '/api/datasets') {
      created = true
      return route.fulfill({ json: dataset })
    }
    if (method === 'POST' && path === '/api/datasets/misses/items') {
      expect(route.request().postDataJSON()).toMatchObject({ run_id: run.id, outcomes: ['incorrect', 'error'] })
      return route.fulfill({ json: [item] })
    }
    const json =
      path === '/api/auth/me'
        ? { auth_enabled: false, user: 'local', role: 'owner', via: 'local', workspace: null, workspaces: [] }
        : path === `/api/runs/${run.id}`
          ? run
          : path.endsWith('/samples')
            ? { total: 1, items: [sample] }
            : path === '/api/datasets'
              ? created
                ? [dataset]
                : []
              : path === '/api/datasets/misses'
                ? { ...dataset, items: [item] }
                : null
    if (!json) throw new Error(`Unexpected API call: ${method} ${path}`)
    await route.fulfill({ json })
  })
  await page.goto(`/runs/${run.id}`)
  await page.getByRole('button', { name: 'Add failures to dataset' }).click()
  await page.locator('#dataset-name').fill('Historical misses')
  await page.getByRole('button', { name: 'Add drafts' }).click()
  await expect(page.getByText(/Added 1 draft item/)).toBeVisible()
  await page.goto('/datasets')
  await page.getByRole('link', { name: 'Historical misses' }).click()
  await page.getByRole('button', { name: /Use benchmark target/ }).click()
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText(/Cannot approve: no split/)).toBeVisible()
  await page.getByRole('combobox', { name: 'Split' }).click()
  await page.getByRole('option', { name: 'Held-out test' }).click()
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText('Item 1: approved', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Export JSONL' })).toBeVisible()
  expect(item.reference_output).toBe('4')
  expect(item.status).toBe('approved')
})
