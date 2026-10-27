import { expect, test, type Page } from '@playwright/test'
import type { CatalogEntry, EvaluationTarget, Run } from '../src/api'

const catalog: CatalogEntry[] = [
  {
    ref: 'findeepindicator',
    name: 'FinDeepIndicator',
    family: 'finance',
    task_count: 3350,
    requires_trace: false,
    experimental: true,
    license: 'Apache-2.0',
    grader: 'Response numeric accuracy',
    description: 'Grades submitted responses.',
  },
  {
    ref: 'bigfinance-public-50',
    name: 'BigFinance public 50',
    family: 'finance',
    task_count: 50,
    requires_trace: true,
    experimental: true,
    license: 'CC-BY-4.0',
    grader: 'Visible trace rubric',
    description: 'Public 50-item subset only.',
  },
]
const targets: EvaluationTarget[] = [
  {
    id: 'response-agent',
    name: 'Response agent',
    version: 'v1',
    origin: 'https://agent.example',
    has_trace_source: false,
  },
  {
    id: 'trace-agent',
    name: 'Trace agent',
    version: 'v2',
    origin: 'https://trace-agent.example',
    has_trace_source: true,
  },
]

const queuedRun: Run = {
  id: 'queued-run',
  group_id: null,
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  finished_at: null,
  status: 'queued',
  benchmark: 'findeepindicator',
  variant_key: 'findeepindicator',
  model: null,
  target_kind: 'external_agent',
  target: { name: 'Response agent', version: 'v1' },
  agent: null,
  resolved_models: [],
  limit: null,
  epochs: 1,
  budget_usd: null,
  content_policy: 'redacted',
  quote_id: null,
  samples_total: null,
  samples_done: 0,
  n_correct: 0,
  n_error: 0,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: null,
  metrics: {},
  log_path: null,
  error: null,
  accuracy: null,
  score: null,
  purpose: 'eval',
}

type MockOptions = {
  role?: string
  targets?: EvaluationTarget[]
  targetError?: boolean
  submissionError?: boolean
  delayTargets?: boolean
}
async function mockApi(page: Page, options: MockOptions = {}) {
  const submissions: Record<string, unknown>[] = []
  let run = queuedRun
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/me')
      return route.fulfill({
        json: {
          auth_enabled: true,
          user: 'test',
          role: options.role ?? 'member',
          via: 'session',
          workspace: { id: 'workspace-a', name: 'A' },
          workspaces: [],
        },
      })
    if (path === '/api/catalog') return route.fulfill({ json: catalog })
    if (path === '/api/evaluation-targets') {
      if (options.delayTargets) await new Promise((resolve) => setTimeout(resolve, 700))
      return route.fulfill(
        options.targetError
          ? { status: 503, json: { detail: 'Target configuration unavailable.' } }
          : { json: options.targets ?? targets },
      )
    }
    if (path === '/api/runs' && route.request().method() === 'POST') {
      const submission = route.request().postDataJSON()
      submissions.push(submission)
      const target = targets.find((item) => item.id === submission.target_id)
      run = { ...queuedRun, benchmark: submission.benchmark, content_policy: submission.content_policy, target }
      return route.fulfill(
        options.submissionError
          ? { status: 422, json: { detail: 'Evaluator configuration unavailable; no agent invoked.' } }
          : { json: run },
      )
    }
    if (path === '/api/runs/queued-run') return route.fulfill({ json: run })
    if (path.endsWith('/samples')) return route.fulfill({ json: { total: 0, items: [] } })
    throw new Error(`Unexpected API call: ${route.request().method()} ${path}`)
  })
  return submissions
}

async function choose(page: Page, id: string, option: string) {
  await page.locator(`#${id}`).click()
  await page.getByRole('option', { name: option, exact: true }).click()
}
async function fillIndicator(page: Page) {
  await page.goto('/new')
  await choose(page, 'benchmark', 'FinDeepIndicator · 3,350 tasks')
  await choose(page, 'target', 'Response agent · v1')
  await page.getByLabel('Judge model IDs', { exact: true }).fill('openai/judge')
  await page.getByLabel('Extractor model ID', { exact: true }).fill('openai/extractor')
}

test('submits one strict evaluation payload with redacted default and explicit consent, then opens detail', async ({
  page,
}) => {
  const submissions = await mockApi(page)
  await fillIndicator(page)
  await expect(page.locator('#content')).toHaveText('Redacted')
  await expect(page.getByText(/strict NDJSON/)).toBeVisible()
  await expect(page.getByText(/Remote agent and evaluator costs are unknown and uncapped/)).toBeVisible()
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  const start = page.getByRole('button', { name: 'Start evaluation' })
  await expect(start).toBeDisabled()
  await page.locator('#sample-ids').fill('case-1, case-2\ncase-3')
  await page.locator('#limit').fill('2')
  await page.locator('#timeout').fill('45')
  await page.locator('#trace-timeout').fill('0')
  await page.locator('#grading-timeout').fill('120')
  await page.getByRole('checkbox').check()
  await start.click()
  await expect(page).toHaveURL(/\/runs\/queued-run$/)
  await expect(page.getByText('queued', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: /findeepindicator.*Response agent/ })).toBeVisible()
  expect(submissions).toEqual([
    {
      benchmark: 'findeepindicator',
      target_id: 'response-agent',
      judge_models: ['openai/judge'],
      extractor_model: 'openai/extractor',
      sample_ids: ['case-1', 'case-2', 'case-3'],
      limit: 2,
      timeout: 45,
      trace_timeout: 0,
      grading_timeout: 120,
      content_policy: 'redacted',
      allow_paid: true,
    },
  ])
})

test('requires one FinDeepIndicator judge and extractor; rejects duplicate IDs and invalid numbers', async ({
  page,
}) => {
  const submissions = await mockApi(page)
  await fillIndicator(page)
  await page.getByRole('checkbox').check()
  const start = page.getByRole('button', { name: 'Start evaluation' })
  await page.locator('#extractor').fill('')
  await expect(start).toBeDisabled()
  await page.locator('#extractor').fill('openai/extractor')
  await page.locator('#judges').fill('openai/a\nopenai/b')
  await expect(page.getByRole('alert').filter({ hasText: 'exactly one judge' })).toBeVisible()
  await expect(start).toBeDisabled()
  await page.locator('#judges').fill('openai/a')
  for (const [id, invalid, valid] of [
    ['limit', '0', '1'],
    ['timeout', '0', '40'],
    ['trace-timeout', '-1', '0'],
    ['grading-timeout', '0', '180'],
  ]) {
    await page.locator(`#${id}`).fill(invalid)
    await expect(start).toBeDisabled()
    await page.locator(`#${id}`).fill(valid)
  }
  await page.locator('#sample-ids').fill('one,one')
  await expect(start).toBeDisabled()
  await page.locator('#sample-ids').fill('one,two')
  await expect(start).toBeEnabled()
  expect(submissions).toEqual([])
})

test('BigFinance blocks a target without traces; switching benchmark omits extractor and optional selection', async ({
  page,
}) => {
  const submissions = await mockApi(page)
  await fillIndicator(page)
  await choose(page, 'benchmark', 'BigFinance public 50 · 50 tasks')
  await expect(page.locator('#extractor')).toHaveCount(0)
  await expect(
    page.getByRole('alert').filter({ hasText: 'BigFinance requires an authorized trace source' }),
  ).toBeVisible()
  await page.getByRole('checkbox').check()
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  await choose(page, 'target', 'Trace agent · v2')
  await page.locator('#judges').fill('anthropic/judge, anthropic/judge')
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  await page.locator('#judges').fill('anthropic/judge, openai/judge')
  await choose(page, 'content', 'Metadata only')
  await page.getByRole('button', { name: 'Start evaluation' }).click()
  await expect(page).toHaveURL(/\/runs\/queued-run$/)
  expect(submissions).toEqual([
    {
      benchmark: 'bigfinance-public-50',
      target_id: 'trace-agent',
      judge_models: ['anthropic/judge', 'openai/judge'],
      timeout: 40,
      trace_timeout: 10,
      grading_timeout: 180,
      content_policy: 'metadata',
      allow_paid: true,
    },
  ])
})

test('no targets shows operator setup guidance without URL or credential inputs', async ({ page }) => {
  await mockApi(page, { targets: [] })
  await page.goto('/new')
  await expect(page.getByText(/No authorized agent targets/)).toBeVisible()
  await expect(page.getByText(/EVERYBENCH_TARGETS_FILE/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  await expect(page.getByLabel(/URL|credential|secret/i)).toHaveCount(0)
})

test('loading and metadata failures disable submission and offer retry', async ({ page }) => {
  await mockApi(page, { targetError: true, delayTargets: true })
  await page.goto('/new')
  await expect(page.getByRole('status')).toHaveText('Loading benchmarks and authorized targets…')
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  await expect(page.getByText(/Target configuration unavailable/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
})

test('submission failures are visible and do not navigate or invent success', async ({ page }) => {
  const submissions = await mockApi(page, { submissionError: true })
  await fillIndicator(page)
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Start evaluation' }).click()
  await expect(page.getByText(/Evaluator configuration unavailable; no agent invoked/)).toBeVisible()
  await expect(page).toHaveURL(/\/new$/)
  expect(submissions).toHaveLength(1)
})

test('viewer is explicitly read-only and cannot submit', async ({ page }) => {
  const submissions = await mockApi(page, { role: 'viewer' })
  await page.goto('/new')
  await expect(page.getByText(/Read-only access/)).toBeVisible()
  await expect(page.locator('#judges')).toBeDisabled()
  await expect(page.getByRole('checkbox')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  expect(submissions).toEqual([])
})

test('catalog exposes experimental finance counts, trace requirements and license metadata only', async ({ page }) => {
  await mockApi(page)
  await page.goto('/catalog')
  await expect(page.getByRole('cell', { name: '3,350', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '50', exact: true })).toBeVisible()
  await expect(page.getByText('Experimental', { exact: true })).toHaveCount(2)
  await expect(page.getByText(/not official leaderboard parity/)).toBeVisible()
  await expect(page.getByText('Complete visible trace required')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'CC-BY-4.0', exact: true })).toBeVisible()
  await expect(page.getByText(/gsm8k|Harbor|Docker|Get quote/)).toHaveCount(0)
})

test('workspace switch clears target selection and cost consent before another submission', async ({ page }) => {
  const submissions = await mockApi(page)
  await page.route('**/api/auth/me', async (route) => {
    const workspace = route.request().headers()['x-everybench-workspace'] === 'workspace-b' ? 'b' : 'a'
    if (workspace === 'b') await new Promise((resolve) => setTimeout(resolve, 400))
    await route.fulfill({
      json: {
        auth_enabled: true,
        user: 'test',
        role: 'member',
        via: 'session',
        workspace: { id: `workspace-${workspace}`, name: workspace.toUpperCase() },
        workspaces: [
          { id: 'workspace-a', name: 'A', role: 'member' },
          { id: 'workspace-b', name: 'B', role: 'member' },
        ],
      },
    })
  })
  await fillIndicator(page)
  await page.getByRole('checkbox').check()
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeEnabled()
  await page.getByRole('combobox', { name: 'Workspace', exact: true }).click()
  await page.getByRole('option', { name: 'B', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  await expect(page.getByRole('status')).toHaveText('Loading benchmarks and authorized targets…')
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  await expect(page.locator('#target')).toHaveText('Choose an authorized target')
  await expect(page.locator('#judges')).toHaveValue('')
  expect(submissions).toEqual([])
})
