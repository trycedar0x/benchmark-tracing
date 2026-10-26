import { expect, test } from '@playwright/test'
import { pct, resolvedModelsNote, runMetric, who } from '../src/lib/format'

test('external target labels use real identity and version, never a fake model', () => {
  expect(who(null, null, { kind: 'external_agent', name: 'Research agent', version: 'v7' })).toBe(
    'External agent · Research agent (v7)',
  )
  expect(who(null, null, { identity: 'my.module.agent' })).toBe('External agent · my.module.agent (unversioned)')
  expect(who(null)).toBe('External agent · Unnamed (unversioned)')
})

test('external primary Score preserves fractions, zero and null without accuracy fallback', () => {
  for (const score of [0.5, 0, null]) {
    const metric = runMetric({ target_kind: 'external_agent', score, accuracy: 0 })
    expect(metric).toEqual({ label: 'Score', value: score })
  }
  expect(pct(runMetric({ target_kind: 'external_agent', score: null, accuracy: 0 }).value)).toBe('–')
  expect(runMetric({ target_kind: 'model', score: 0.5, accuracy: 1 })).toEqual({ label: 'Accuracy', value: 1 })
  expect(runMetric({ score: null, accuracy: 0.25 })).toEqual({ label: 'Accuracy', value: 0.25 })
})

test('resolved model subtitle distinguishes external composition from legacy revisions', () => {
  expect(resolvedModelsNote({ target_kind: 'external_agent', resolved_models: ['a', 'b'] })).toBe('multiple models')
  expect(resolvedModelsNote({ target_kind: 'model', resolved_models: ['a', 'b'] })).toBe('more than one revision')
  expect(resolvedModelsNote({ resolved_models: ['a', 'b'] })).toBe('more than one revision')
  expect(resolvedModelsNote({ target_kind: 'external_agent', resolved_models: ['a'] })).toBeUndefined()
})

test('legacy model and custom Harbor labels remain unchanged', () => {
  expect(who('openai/pinned-model')).toBe('openai/pinned-model')
  expect(who('openai/pinned-model', 'my.module:HarborAgent')).toBe('openai/pinned-model · HarborAgent')
  expect(who('openai/pinned-model', null, {})).toBe('openai/pinned-model')
})
