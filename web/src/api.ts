export type Run = {
  id: string
  group_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  status: string
  benchmark: string
  variant_key: string
  model: string
  resolved_models: string[]
  limit: number | null
  epochs: number
  budget_usd: number | null
  content_policy: string
  quote_id: string | null
  samples_total: number | null
  samples_done: number
  n_correct: number
  n_error: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  metrics: Record<string, Record<string, number>>
  log_path: string | null
  error: string | null
  accuracy: number | null
  purpose: string
  manifest?: Record<string, unknown>
  outcomes?: Record<string, number>
}

export type Sample = {
  sample_id: string
  epoch: number
  outcome: string
  score: number | null
  score_raw: string | null
  scorer: string | null
  answer: string | null
  explanation: string | null
  target: string | null
  input: string | null
  output: string | null
  error: string | null
  input_tokens: number
  output_tokens: number
  total_time: number | null
  trace_id: string | null
  run_id: string
}

export type CatalogEntry = {
  ref: string
  id: string
  version: number
  family: string
  variant: string
  task: string
  grader: string
  task_count: number | null
  license: string | null
  dataset: string | null
  requires: string[]
  sandbox: string | null
  offline: boolean
  description: string | null
  notes: string | null
  variant_key: string
}

export type ModelPrice = {
  model: string
  mock: boolean
  price: { input: number; output: number; as_of: string | null; source: string | null }
}

export type ModelEstimate = {
  samples_measured: number
  sample_errors?: number
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  cost_usd_range?: [number, number]
  note?: string
  sample_run_id?: string
  sample_status?: string
}

export type Quote = {
  id: string
  created_at: string
  benchmark: string
  models: string[]
  limit: number | null
  sample_size: number
  samples_planned: number | null
  estimate: Record<string, ModelEstimate>
  cap_usd: number | null
  status: string
  approved_at: string | null
  approved_by: string | null
}

export type Span = {
  trace_id: string
  span_id: string
  parent_id: string | null
  run_id: string | null
  name: string
  kind: string
  start_time: string | null
  end_time: string | null
  status: string
  attributes: Record<string, unknown>
  content: Record<string, unknown> | null
  content_state: string
  source: string
}

export type TraceHeader = {
  trace_id: string
  run_id: string | null
  sample_id: string | null
  name: string | null
  source: string
  source_ref: string | null
  created_at: string
  start_time: string | null
  end_time: string | null
  span_count: number
  state: string
  attributes: Record<string, unknown>
}

export type TraceDetail = { trace: TraceHeader; sample: Sample | null; spans: Span[] }

export type PairRow = {
  sample_id: string
  epoch: number
  a_outcome: string
  b_outcome: string
  a_score: number | null
  b_score: number | null
  change: string
  a_trace_id: string | null
  b_trace_id: string | null
}

export type Comparison = {
  run_a: { id: string; model: string; benchmark: string; status: string; resolved_models: string[] }
  run_b: { id: string; model: string; benchmark: string; status: string; resolved_models: string[] }
  compatibility: { comparable: boolean; blocking: string[]; warnings: string[] }
  n_paired: number
  n_scored_pairs: number
  mean_a: number | null
  mean_b: number | null
  delta: number | null
  delta_ci95: [number, number] | null
  mcnemar_p: number | null
  discordant: { improvements?: number; regressions?: number }
  errors: { a_only?: number; b_only?: number; both?: number }
  missing: { only_a?: number; only_b?: number }
  rows: PairRow[]
}

export type DiffStep = {
  span_id: string
  kind: string
  name: string
  status: string
  summary: string
  output: Record<string, unknown>
}

export type TraceDiff = {
  a: { trace_id: string; run_id: string | null; name: string | null; outcome: string | null; score: number | null }
  b: { trace_id: string; run_id: string | null; name: string | null; outcome: string | null; score: number | null }
  pairs: {
    op: 'same' | 'changed' | 'only_a' | 'only_b'
    a: DiffStep | null
    b: DiffStep | null
    differences: string[]
  }[]
  first_divergence: number | null
  summary: string
}

export type ImportRun = {
  id: string
  created_at: string
  finished_at: string | null
  source: string
  project: string | null
  usage_rights: string
  content_policy: string
  status: string
  traces_imported: number
  spans_imported: number
  error: string | null
}

export type Dataset = {
  id: string
  name: string
  description: string | null
  created_at: string
  counts?: Record<string, number>
}

export type DatasetItem = {
  id: number
  dataset_id: string
  source_trace_id: string
  source: string
  input: string | null
  historical_output: string | null
  historical_score: number | null
  reference_output: string | null
  split: string
  status: string
  usage_rights: string
  provenance: Record<string, unknown>
  notes: string | null
  reviewed_at: string | null
  reviewed_by: string | null
}

export type Me = {
  auth_enabled: boolean
  user: string
  role: string
  via: string
  workspace: { id: string; name: string } | null
  workspaces: { id: string; name: string; role: string }[]
}

export type ApiKeyRow = {
  id: string
  name: string
  prefix: string
  role: string
  created_by: string | null
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let workspaceId: string | null = null
try {
  workspaceId = localStorage.getItem('everyeval.workspace') ?? localStorage.getItem('benchtrace.workspace')
} catch {
  /* storage unavailable */
}

export function selectWorkspace(id: string) {
  workspaceId = id
  try {
    localStorage.setItem('everyeval.workspace', id)
  } catch {
    /* storage unavailable */
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(workspaceId ? { 'X-EveryEval-Workspace': workspaceId } : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  catalog: () => request<CatalogEntry[]>('/api/catalog'),
  models: () => request<ModelPrice[]>('/api/models'),
  runs: (params: Record<string, string> = {}) => request<Run[]>(`/api/runs?${new URLSearchParams(params)}`),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  samples: (id: string, params: Record<string, string>) =>
    request<{ total: number; items: Sample[] }>(`/api/runs/${id}/samples?${new URLSearchParams(params)}`),
  startRuns: (body: Record<string, unknown>) =>
    request<Run[]>('/api/runs', { method: 'POST', body: JSON.stringify(body) }),
  cancel: (id: string) => request<Run>(`/api/runs/${id}/cancel`, { method: 'POST' }),
  createQuote: (body: Record<string, unknown>) =>
    request<Quote>('/api/quotes', { method: 'POST', body: JSON.stringify(body) }),
  quote: (id: string) => request<Quote>(`/api/quotes/${id}`),
  approveQuote: (id: string, cap_usd: number | null) =>
    request<Quote>(`/api/quotes/${id}/approve`, { method: 'POST', body: JSON.stringify({ cap_usd }) }),
  compare: (a: string, b: string, force = false) =>
    request<Comparison>(`/api/compare?${new URLSearchParams({ a, b, force: String(force) })}`),
  trace: (id: string) => request<TraceDetail>(`/api/traces/${id}`),
  me: () => request<Me>('/api/auth/me'),
  login: (email: string, password: string) =>
    request<{ ok: boolean }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  keys: () => request<ApiKeyRow[]>('/api/keys'),
  createKey: (name: string, role: string) =>
    request<{ key: string }>('/api/keys', { method: 'POST', body: JSON.stringify({ name, role }) }),
  revokeKey: (id: string) => request<{ ok: boolean }>(`/api/keys/${id}`, { method: 'DELETE' }),
  secrets: () => request<{ name: string; updated_at: string }[]>('/api/secrets'),
  setSecret: (name: string, value: string) =>
    request<{ ok: boolean }>(`/api/secrets/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  deleteSecret: (name: string) =>
    request<{ ok: boolean }>(`/api/secrets/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  imports: () => request<ImportRun[]>('/api/imports'),
  startImport: (body: Record<string, unknown>) =>
    request<ImportRun>('/api/imports', { method: 'POST', body: JSON.stringify(body) }),
  datasets: () => request<Dataset[]>('/api/datasets'),
  dataset: (id: string) => request<Dataset & { items: DatasetItem[] }>(`/api/datasets/${id}`),
  createDataset: (body: Record<string, unknown>) =>
    request<Dataset>('/api/datasets', { method: 'POST', body: JSON.stringify(body) }),
  addItems: (id: string, body: Record<string, unknown>) =>
    request<DatasetItem[]>(`/api/datasets/${id}/items`, { method: 'POST', body: JSON.stringify(body) }),
  reviewItem: (id: string, itemId: number, body: Record<string, unknown>) =>
    request<DatasetItem>(`/api/datasets/${id}/items/${itemId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  traceDiff: (a: string, b: string) => request<TraceDiff>(`/api/trace-diff?${new URLSearchParams({ a, b })}`),
  traces: (params: Record<string, string> = {}) => request<TraceHeader[]>(`/api/traces?${new URLSearchParams(params)}`),
}

export const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'budget_exceeded'])
