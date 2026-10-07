import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Ban, GitCompare } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Stat } from '@/components/stat'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Field, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { errorMessage, pct } from '@/lib/format'
import { api } from '../api'

const FILTERS = ['changed', 'regression', 'improvement', 'error', 'all']

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
    filter === 'all'
      ? true
      : filter === 'changed'
        ? ['regression', 'improvement', 'error'].includes(r.change)
        : r.change === filter,
  )

  return (
    <div className="grid gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">Compare runs</h1>
      <Card>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {(['a', 'b'] as const).map((key) => (
            <Field key={key}>
              <FieldLabel htmlFor={`run-${key}`}>Run {key.toUpperCase()}</FieldLabel>
              <Select value={key === 'a' ? a : b} onValueChange={(v) => set(key, v)}>
                <SelectTrigger id={`run-${key}`} className="w-full">
                  <SelectValue placeholder="Choose a run" />
                </SelectTrigger>
                <SelectContent>
                  {runs.data?.map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {r.id} · {r.benchmark} · {r.model} · {r.status}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ))}
        </CardContent>
      </Card>
      {result.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(result.error)}</AlertDescription>
        </Alert>
      )}
      {!a || !b ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <GitCompare />
            </EmptyMedia>
            <EmptyTitle>Choose two runs</EmptyTitle>
            <EmptyDescription>Runs must use the same benchmark variant to be compared directly.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        c && (
          <>
            {c.compatibility.blocking.map((m) => (
              <Alert key={m} variant="destructive">
                <Ban />
                <AlertTitle>Not directly comparable</AlertTitle>
                <AlertDescription>{m}</AlertDescription>
              </Alert>
            ))}
            {c.compatibility.warnings.map((m) => (
              <Alert key={m}>
                <AlertTriangle />
                <AlertDescription>{m}</AlertDescription>
              </Alert>
            ))}
            {!c.compatibility.comparable && !force && (
              <div>
                <Button variant="outline" onClick={() => set('force', '1')}>
                  Compare anyway (exploratory)
                </Button>
              </div>
            )}
            {c.delta !== null && (
              <Card>
                <CardHeader>
                  <CardTitle>{c.run_a.benchmark}</CardTitle>
                  <CardDescription>
                    A = <span className="font-mono">{c.run_a.model}</span>, B ={' '}
                    <span className="font-mono">{c.run_b.model}</span>
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-6">
                  <Stat label="Score A" value={pct(c.mean_a)} sub={`${c.n_scored_pairs} paired samples`} />
                  <Stat label="Score B" value={pct(c.mean_b)} />
                  <Stat
                    label="Δ B − A"
                    value={
                      <span className={c.delta < 0 ? 'text-destructive' : undefined}>
                        {c.delta > 0 ? '+' : ''}
                        {(c.delta * 100).toFixed(1)} pts
                      </span>
                    }
                    sub={
                      c.delta_ci95
                        ? `95% CI ${(c.delta_ci95[0] * 100).toFixed(1)} to ${(c.delta_ci95[1] * 100).toFixed(1)}`
                        : undefined
                    }
                  />
                  <Stat
                    label="McNemar p"
                    value={c.mcnemar_p === null ? '–' : c.mcnemar_p.toPrecision(3)}
                    sub={c.mcnemar_p !== null && c.mcnemar_p < 0.05 ? 'significant at 5%' : 'not significant at 5%'}
                  />
                  <Stat
                    label="Changes"
                    value={`+${c.discordant.improvements ?? 0} / −${c.discordant.regressions ?? 0}`}
                    sub="improvements / regressions"
                  />
                  <Stat
                    label="Errors"
                    value={`${c.errors.a_only ?? 0} / ${c.errors.b_only ?? 0}`}
                    sub={`A only / B only, ${c.errors.both ?? 0} both`}
                  />
                </CardContent>
              </Card>
            )}
            {c.rows.length > 0 && (
              <Card className="pb-0">
                <CardHeader>
                  <CardTitle>Samples ({rows.length})</CardTitle>
                  <CardAction>
                    <ToggleGroup
                      type="single"
                      variant="outline"
                      size="sm"
                      value={filter}
                      onValueChange={(v) => v && setFilter(v)}
                    >
                      {FILTERS.map((f) => (
                        <ToggleGroupItem key={f} value={f}>
                          {f}
                        </ToggleGroupItem>
                      ))}
                    </ToggleGroup>
                  </CardAction>
                </CardHeader>
                <CardContent className="px-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="pl-6">Sample</TableHead>
                        <TableHead>Change</TableHead>
                        <TableHead>A</TableHead>
                        <TableHead>B</TableHead>
                        <TableHead className="pr-6">Traces</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((r) => (
                        <TableRow key={`${r.sample_id}-${r.epoch}`}>
                          <TableCell className="pl-6 font-mono text-xs">{r.sample_id}</TableCell>
                          <TableCell>
                            <StatusBadge value={r.change} />
                          </TableCell>
                          <TableCell>
                            <StatusBadge value={r.a_outcome} />
                          </TableCell>
                          <TableCell>
                            <StatusBadge value={r.b_outcome} />
                          </TableCell>
                          <TableCell className="space-x-3 pr-6 whitespace-nowrap">
                            {r.a_trace_id && r.b_trace_id && (
                              <Button variant="link" size="sm" className="h-auto p-0" asChild>
                                <Link to={`/diff?a=${r.a_trace_id}&b=${r.b_trace_id}`}>Diff</Link>
                              </Button>
                            )}
                            {r.a_trace_id && (
                              <Button variant="link" size="sm" className="h-auto p-0" asChild>
                                <Link to={`/traces/${r.a_trace_id}`}>A</Link>
                              </Button>
                            )}
                            {r.b_trace_id && (
                              <Button variant="link" size="sm" className="h-auto p-0" asChild>
                                <Link to={`/traces/${r.b_trace_id}`}>B</Link>
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}
          </>
        )
      )}
    </div>
  )
}
