import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, TERMINAL } from '../api'
import { Badge, Button, Card, ErrorNote, Progress, Stat, money, num, pct, td, th } from '../ui'

const OUTCOMES = ['correct', 'incorrect', 'partial', 'error', 'unscored', 'cancelled']

export default function RunPage() {
  const { runId = '' } = useParams()
  const qc = useQueryClient()
  const [outcome, setOutcome] = useState('')
  const [page, setPage] = useState(0)
  const pageSize = 50

  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.run(runId),
    refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 1500),
  })
  const live = run.data && !TERMINAL.has(run.data.status)
  const samples = useQuery({
    queryKey: ['samples', runId, outcome, page],
    queryFn: () =>
      api.samples(runId, { offset: String(page * pageSize), limit: String(pageSize), ...(outcome ? { outcome } : {}) }),
    refetchInterval: live ? 2000 : false,
  })
  const cancel = useMutation({
    mutationFn: () => api.cancel(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['run', runId] }),
  })

  if (run.error) return <ErrorNote error={run.error} />
  const r = run.data
  if (!r) return <div className="text-muted">Loading…</div>
  const versions = (r.manifest?.versions ?? {}) as Record<string, string>

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-xs text-muted">{r.id}</div>
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">
            {r.benchmark} <span className="text-muted">·</span> <span className="font-mono text-base">{r.model}</span>
            <Badge value={r.status} />
          </h1>
        </div>
        {live && (
          <Button kind="danger" disabled={cancel.isPending || r.status === 'cancelling'} onClick={() => cancel.mutate()}>
            Cancel run
          </Button>
        )}
      </div>
      {r.error && r.status !== 'succeeded' && (
        <pre className="overflow-x-auto rounded border border-warn/40 bg-warn-bg p-3 text-xs whitespace-pre-wrap text-warn">{r.error}</pre>
      )}
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Accuracy" value={pct(r.accuracy)} sub={`${r.n_correct} correct`} />
          <Stat label="Progress" value={<Progress done={r.samples_done} total={r.samples_total} />} />
          <Stat label="Errors" value={r.n_error} sub="excluded from accuracy" />
          <Stat label="Tokens" value={num(r.input_tokens + r.output_tokens)} sub={`${num(r.input_tokens)} in / ${num(r.output_tokens)} out`} />
          <Stat label="Est. cost" value={money(r.cost_usd)} sub={r.budget_usd ? `cap ${money(r.budget_usd)}` : 'no cap'} />
          <Stat label="Resolved model" value={<span className="font-mono text-sm">{r.resolved_models.join(', ') || '–'}</span>} sub={r.resolved_models.length > 1 ? 'more than one revision' : undefined} />
        </div>
      </Card>
      <details className="rounded-md border border-line bg-surface px-4 py-2.5 text-[13px]">
        <summary className="cursor-pointer font-semibold">Manifest</summary>
        <dl className="mt-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1">
          <dt className="text-muted">Variant</dt>
          <dd className="font-mono">{r.variant_key}</dd>
          <dt className="text-muted">Content policy</dt>
          <dd>{r.content_policy}</dd>
          <dt className="text-muted">Limit / epochs</dt>
          <dd>
            {r.limit ?? 'all'} / {r.epochs}
          </dd>
          <dt className="text-muted">Versions</dt>
          <dd>{Object.entries(versions).map(([k, v]) => `${k} ${v}`).join(', ') || '–'}</dd>
          <dt className="text-muted">Metrics</dt>
          <dd className="font-mono break-all">{JSON.stringify(r.metrics)}</dd>
          <dt className="text-muted">Inspect log</dt>
          <dd className="font-mono break-all">{r.log_path ?? '–'}</dd>
        </dl>
      </details>
      <Card
        title={`Samples (${samples.data?.total ?? '…'})`}
        actions={
          <div className="flex flex-wrap gap-1">
            {['', ...OUTCOMES].map((o) => {
              const count = o ? (r.outcomes?.[o] ?? 0) : r.samples_done
              if (o && !count) return null
              return (
                <button
                  key={o || 'all'}
                  onClick={() => {
                    setOutcome(o)
                    setPage(0)
                  }}
                  className={`rounded px-2 py-0.5 text-xs ${outcome === o ? 'bg-accent text-accent-fg' : 'bg-sunken text-muted hover:text-fg'}`}
                >
                  {o || 'all'} {count}
                </button>
              )
            })}
          </div>
        }
      >
        <div className="-m-4 overflow-x-auto">
          <table className="w-full min-w-[760px] text-[13px]">
            <thead className="border-b border-line">
              <tr>
                <th className={th}>Sample</th>
                <th className={th}>Outcome</th>
                <th className={th}>Answer</th>
                <th className={th}>Target</th>
                <th className={`${th} text-right`}>Tokens</th>
                <th className={th}>Trace</th>
              </tr>
            </thead>
            <tbody>
              {samples.data?.items.map((s) => (
                <tr key={`${s.sample_id}-${s.epoch}`} className="border-b border-line last:border-0 hover:bg-sunken">
                  <td className={`${td} font-mono text-xs`}>
                    {s.sample_id}
                    {r.epochs > 1 && <span className="text-muted"> #{s.epoch}</span>}
                  </td>
                  <td className={td}>
                    <Badge value={s.outcome} />
                  </td>
                  <td className={`${td} max-w-[28ch] truncate`} title={s.answer ?? s.error ?? ''}>
                    {s.answer ?? s.error ?? (r.content_policy === 'metadata' ? <span className="text-muted">withheld</span> : '–')}
                  </td>
                  <td className={`${td} max-w-[20ch] truncate`} title={s.target ?? ''}>
                    {s.target ?? '–'}
                  </td>
                  <td className={`${td} tabular text-right`}>{num(s.input_tokens + s.output_tokens)}</td>
                  <td className={td}>
                    {s.trace_id && (
                      <Link className="text-accent hover:underline" to={`/traces/${s.trace_id}`}>
                        view
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {samples.data && samples.data.total > pageSize && (
          <div className="mt-6 flex items-center gap-2">
            <Button disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span className="text-xs text-muted">
              {page * pageSize + 1}–{Math.min(samples.data.total, (page + 1) * pageSize)} of {samples.data.total}
            </span>
            <Button disabled={(page + 1) * pageSize >= samples.data.total} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        )}
      </Card>
    </div>
  )
}
