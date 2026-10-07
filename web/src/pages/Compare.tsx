import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { Badge, Button, Card, Empty, ErrorNote, Stat, inputCls, pct, td, th } from '../ui'

export default function ComparePage() {
  const [params, setParams] = useSearchParams()
  const a = params.get('a') ?? ''
  const b = params.get('b') ?? ''
  const force = params.get('force') === '1'
  const [filter, setFilter] = useState('changed')
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => api.runs() })
  const result = useQuery({
    queryKey: ['compare', a, b, force],
    queryFn: () => api.compare(a, b, force),
    enabled: !!a && !!b,
  })

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  const c = result.data
  const rows = (c?.rows ?? []).filter((r) =>
    filter === 'all' ? true : filter === 'changed' ? ['regression', 'improvement', 'error'].includes(r.change) : r.change === filter,
  )

  return (
    <div className="grid gap-4">
      <h1 className="text-xl font-semibold">Compare runs</h1>
      <Card>
        <div className="grid gap-3 sm:grid-cols-2">
          {(['a', 'b'] as const).map((key) => (
            <label key={key} className="grid gap-1">
              <span className="text-xs font-semibold text-muted">Run {key.toUpperCase()}</span>
              <select id={`run-${key}`} className={inputCls} value={key === 'a' ? a : b} onChange={(e) => set(key, e.target.value)}>
                <option value="">Choose a run…</option>
                {runs.data?.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.id} · {r.benchmark} · {r.model} · {r.status}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </Card>
      <ErrorNote error={result.error} />
      {!a || !b ? (
        <Empty>Choose two runs on the same benchmark variant.</Empty>
      ) : (
        c && (
          <>
            {(c.compatibility.blocking.length > 0 || c.compatibility.warnings.length > 0) && (
              <div className="grid gap-2">
                {c.compatibility.blocking.map((m) => (
                  <div key={m} className="rounded border border-bad/40 bg-bad-bg px-3 py-2 text-sm text-bad">
                    <strong>Blocked:</strong> {m}
                  </div>
                ))}
                {c.compatibility.warnings.map((m) => (
                  <div key={m} className="rounded border border-warn/40 bg-warn-bg px-3 py-2 text-sm text-warn">
                    {m}
                  </div>
                ))}
                {!c.compatibility.comparable && !force && (
                  <div>
                    <Button onClick={() => set('force', '1')}>Compare anyway (exploratory)</Button>
                  </div>
                )}
              </div>
            )}
            {c.delta !== null && (
              <Card title={`${c.run_a.benchmark}: A = ${c.run_a.model}, B = ${c.run_b.model}`}>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                  <Stat label="Score A" value={pct(c.mean_a)} sub={`${c.n_scored_pairs} paired samples`} />
                  <Stat label="Score B" value={pct(c.mean_b)} />
                  <Stat
                    label="Δ B − A"
                    value={<span className={c.delta < 0 ? 'text-bad' : c.delta > 0 ? 'text-good' : ''}>{(c.delta * 100).toFixed(1)} pts</span>}
                    sub={c.delta_ci95 ? `95% CI ${(c.delta_ci95[0] * 100).toFixed(1)} to ${(c.delta_ci95[1] * 100).toFixed(1)}` : undefined}
                  />
                  <Stat label="McNemar p" value={c.mcnemar_p === null ? '–' : c.mcnemar_p.toPrecision(3)} sub={c.mcnemar_p !== null && c.mcnemar_p < 0.05 ? 'significant at 5%' : 'not significant at 5%'} />
                  <Stat label="Changes" value={`+${c.discordant.improvements ?? 0} / −${c.discordant.regressions ?? 0}`} sub="improvements / regressions" />
                  <Stat label="Errors" value={`${c.errors.a_only ?? 0} / ${c.errors.b_only ?? 0}`} sub={`A only / B only, ${c.errors.both ?? 0} both`} />
                </div>
              </Card>
            )}
            {c.rows.length > 0 && (
              <Card
                title={`Samples (${rows.length})`}
                actions={
                  <div className="flex flex-wrap gap-1">
                    {['changed', 'regression', 'improvement', 'error', 'all'].map((f) => (
                      <button
                        key={f}
                        onClick={() => setFilter(f)}
                        className={`rounded px-2 py-0.5 text-xs ${filter === f ? 'bg-accent text-accent-fg' : 'bg-sunken text-muted hover:text-fg'}`}
                      >
                        {f}
                      </button>
                    ))}
                  </div>
                }
              >
                <div className="-m-4 overflow-x-auto">
                  <table className="w-full min-w-[640px] text-[13px]">
                    <thead className="border-b border-line">
                      <tr>
                        <th className={th}>Sample</th>
                        <th className={th}>Change</th>
                        <th className={th}>A</th>
                        <th className={th}>B</th>
                        <th className={th}>Traces</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={`${r.sample_id}-${r.epoch}`} className="border-b border-line last:border-0 hover:bg-sunken">
                          <td className={`${td} font-mono text-xs`}>{r.sample_id}</td>
                          <td className={td}>
                            <Badge value={r.change} />
                          </td>
                          <td className={td}>
                            <Badge value={r.a_outcome} />
                          </td>
                          <td className={td}>
                            <Badge value={r.b_outcome} />
                          </td>
                          <td className={`${td} whitespace-nowrap`}>
                            {r.a_trace_id && r.b_trace_id && (
                              <Link className="mr-3 text-accent hover:underline" to={`/diff?a=${r.a_trace_id}&b=${r.b_trace_id}`}>
                                diff
                              </Link>
                            )}
                            {r.a_trace_id && (
                              <Link className="mr-3 text-accent hover:underline" to={`/traces/${r.a_trace_id}`}>
                                A
                              </Link>
                            )}
                            {r.b_trace_id && (
                              <Link className="text-accent hover:underline" to={`/traces/${r.b_trace_id}`}>
                                B
                              </Link>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>
        )
      )}
    </div>
  )
}
