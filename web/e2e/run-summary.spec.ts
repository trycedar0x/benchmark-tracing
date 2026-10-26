import { expect, test } from '@playwright/test'
import type { Run } from '../src/api'

function runFixture(external: boolean, score: number | null): Run {
  return {
    id: 'run_summary',
    group_id: null,
    created_at: '2026-01-01T00:00:00Z',
    started_at: null,
    finished_at: null,
    status: 'succeeded',
    benchmark: 'synthetic',
    variant_key: 'synthetic-v1',
    model: external ? null : 'legacy-model',
    target_kind: external ? 'external_agent' : 'model',
    target: external ? { kind: 'external_agent', name: 'Research agent', version: 'v7' } : {},
    agent: null,
    resolved_models: ['model-a', 'model-b'],
    limit: null,
    epochs: 1,
    budget_usd: null,
    content_policy: 'full',
    quote_id: null,
    samples_total: 3,
    samples_done: 3,
    n_correct: external ? 0 : 1,
    n_error: 1,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: null,
    metrics: {},
    log_path: null,
    error: null,
    accuracy: external ? 0 : 0.5,
    score,
    purpose: 'eval',
  }
}

for (const external of [true, false]) {
  test(`${external ? 'external Score' : 'legacy Accuracy'} renders consistently on Runs, Run and Compare`, async ({
    page,
  }) => {
    const run = runFixture(external, external ? 0.5 : null)
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const json =
        path === '/api/auth/me'
          ? { auth_enabled: false, user: 'local', role: 'owner', via: 'local', workspace: null, workspaces: [] }
          : path === '/api/runs'
            ? [run]
            : path.endsWith('/samples')
              ? { total: 0, items: [] }
              : path === '/api/compare'
                ? {
                    run_a: run,
                    run_b: run,
                    compatibility: { comparable: true, blocking: [], warnings: [] },
                    mean_a: 0.5,
                    mean_b: 0.5,
                    delta: 0,
                    n_scored_pairs: 2,
                    delta_ci95: [0, 0],
                    mcnemar_p: null,
                    discordant: {},
                    errors: { both: 1 },
                    rows: [],
                  }
                : run
      await route.fulfill({ json })
    })
    await page.goto('/')
    const row = page.getByRole('row').filter({ hasText: run.id })
    await expect(row.getByText('50.0%', { exact: true })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: external ? 'Score' : 'Accuracy', exact: true })).toBeVisible()
    await page.goto(`/runs/${run.id}`)
    const metric = page.getByText(external ? 'Score' : 'Accuracy', { exact: true }).locator('..')
    await expect(metric.getByText('50.0%', { exact: true })).toBeVisible()
    await expect(page.getByText(external ? 'multiple models' : 'more than one revision', { exact: true })).toBeVisible()
    await expect(page.getByText(external ? 'more than one revision' : 'multiple models', { exact: true })).toHaveCount(
      0,
    )
    if (external) await expect(metric.getByText('0.0% fully-correct accuracy', { exact: true })).toBeVisible()
    await page.goto(`/compare?a=${run.id}&b=${run.id}`)
    for (const label of ['Score A', 'Score B']) {
      await expect(
        page.getByText(label, { exact: true }).locator('..').getByText('50.0%', { exact: true }),
      ).toBeVisible()
    }
    await expect(page.getByText('2 paired samples', { exact: true })).toBeVisible()
  })
}

test('unscored external run displays unknown Score, never synthetic zero or accuracy', async ({ page }) => {
  const run = { ...runFixture(true, null), status: 'failed', n_error: 3, accuracy: null }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    await route.fulfill({
      json:
        path === '/api/auth/me'
          ? { auth_enabled: false, workspaces: [] }
          : path === '/api/runs'
            ? [run]
            : path.endsWith('/samples')
              ? { total: 0, items: [] }
              : run,
    })
  })
  await page.goto('/')
  const row = page.getByRole('row').filter({ hasText: run.id })
  await expect(row.locator('td[title="Score"]')).toHaveText('–')
  await page.goto(`/runs/${run.id}`)
  const metric = page.getByText('Score', { exact: true }).locator('..')
  await expect(metric.getByText('–', { exact: true })).toBeVisible()
  await expect(metric.getByText('0.0%', { exact: true })).toHaveCount(0)
})
