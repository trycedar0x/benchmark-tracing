import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Square } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AddToDataset } from '@/components/add-to-dataset'
import { Stat } from '@/components/stat'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { errorMessage, money, num, pct } from '@/lib/format'
import { api, TERMINAL } from '../api'

const OUTCOMES = ['correct', 'incorrect', 'partial', 'error', 'unscored', 'cancelled']
const PAGE_SIZE = 50

export default function RunPage() {
  const { runId = '' } = useParams()
  const qc = useQueryClient()
  const [outcome, setOutcome] = useState('all')
  const [page, setPage] = useState(0)

  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.run(runId),
    refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 1500),
  })
  const live = run.data && !TERMINAL.has(run.data.status)
  const samples = useQuery({
    queryKey: ['samples', runId, outcome, page],
    queryFn: () =>
      api.samples(runId, {
        offset: String(page * PAGE_SIZE),
        limit: String(PAGE_SIZE),
        ...(outcome !== 'all' ? { outcome } : {}),
      }),
    refetchInterval: live ? 2000 : false,
  })
  const cancel = useMutation({
    mutationFn: () => api.cancel(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['run', runId] }),
  })

  if (run.error)
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(run.error)}</AlertDescription>
      </Alert>
    )
  const r = run.data
  if (!r) return <Skeleton className="h-64 w-full" />
  const versions = (r.manifest?.versions ?? {}) as Record<string, string>
  const total = samples.data?.total ?? 0

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted-foreground">{r.id}</p>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
            {r.benchmark} <span className="font-mono text-lg font-normal text-muted-foreground">{r.model}</span>
            <StatusBadge value={r.status} />
          </h1>
        </div>
        <div className="flex gap-2">
          {!live && (r.outcomes?.incorrect || r.outcomes?.error) ? (
            <AddToDataset runId={r.id} outcomes={['incorrect', 'error']} label="Add failures to dataset" />
          ) : null}
          {live && (
            <Button
              variant="destructive"
              disabled={cancel.isPending || r.status === 'cancelling'}
              onClick={() => cancel.mutate()}
            >
              <Square /> Cancel run
            </Button>
          )}
        </div>
      </div>
      {r.error && r.status !== 'succeeded' && (
        <Alert variant={r.status === 'failed' ? 'destructive' : 'default'}>
          <AlertTitle>{r.status === 'budget_exceeded' ? 'Stopped at budget cap' : 'Run did not finish'}</AlertTitle>
          <AlertDescription>
            <pre className="whitespace-pre-wrap font-mono text-xs">{r.error}</pre>
          </AlertDescription>
        </Alert>
      )}
      <Card>
        <CardContent className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Accuracy" value={pct(r.accuracy)} sub={`${r.n_correct} correct`} />
          <Stat
            label="Progress"
            value={`${r.samples_done}/${r.samples_total ?? '?'}`}
            sub={<Progress value={r.samples_total ? (100 * r.samples_done) / r.samples_total : 0} className="mt-1" />}
          />
          <Stat label="Errors" value={r.n_error} sub="excluded from accuracy" />
          <Stat
            label="Tokens"
            value={num(r.input_tokens + r.output_tokens)}
            sub={`${num(r.input_tokens)} in / ${num(r.output_tokens)} out`}
          />
          <Stat
            label="Est. cost"
            value={money(r.cost_usd)}
            sub={r.budget_usd ? `cap ${money(r.budget_usd)}` : 'no cap'}
          />
          <Stat
            label="Resolved model"
            value={<span className="font-mono text-sm">{r.resolved_models.join(', ') || '–'}</span>}
            sub={r.resolved_models.length > 1 ? 'more than one revision' : undefined}
          />
        </CardContent>
      </Card>
      <Collapsible>
        <Card className="gap-0 py-0">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" className="w-full justify-between rounded-xl px-6 py-4">
              Manifest <ChevronDown />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <CardContent className="pb-6">
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Variant</dt>
                <dd className="font-mono">{r.variant_key}</dd>
                <dt className="text-muted-foreground">Content policy</dt>
                <dd>{r.content_policy}</dd>
                <dt className="text-muted-foreground">Limit / epochs</dt>
                <dd>
                  {r.limit ?? 'all'} / {r.epochs}
                </dd>
                <dt className="text-muted-foreground">Versions</dt>
                <dd>
                  {Object.entries(versions)
                    .map(([k, v]) => `${k} ${v}`)
                    .join(', ') || '–'}
                </dd>
                <dt className="text-muted-foreground">Metrics</dt>
                <dd className="font-mono break-all">{JSON.stringify(r.metrics)}</dd>
                <dt className="text-muted-foreground">Inspect log</dt>
                <dd className="font-mono break-all">{r.log_path ?? '–'}</dd>
              </dl>
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
      <Card className="pb-0">
        <CardHeader>
          <CardTitle>Samples ({samples.data?.total ?? '…'})</CardTitle>
          <CardAction>
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={outcome}
              onValueChange={(v) => {
                if (v) {
                  setOutcome(v)
                  setPage(0)
                }
              }}
            >
              <ToggleGroupItem value="all">all {r.samples_done}</ToggleGroupItem>
              {OUTCOMES.filter((o) => r.outcomes?.[o]).map((o) => (
                <ToggleGroupItem key={o} value={o}>
                  {o} {r.outcomes?.[o]}
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
                <TableHead>Outcome</TableHead>
                <TableHead>Answer</TableHead>
                <TableHead>Target</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="pr-6">Trace</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {samples.data?.items.map((s) => (
                <TableRow key={`${s.sample_id}-${s.epoch}`}>
                  <TableCell className="pl-6 font-mono text-xs">
                    {s.sample_id}
                    {r.epochs > 1 && <span className="text-muted-foreground"> #{s.epoch}</span>}
                  </TableCell>
                  <TableCell>
                    <StatusBadge value={s.outcome} />
                  </TableCell>
                  <TableCell className="max-w-[28ch] truncate" title={s.answer ?? s.error ?? ''}>
                    {s.answer ?? s.error ?? (r.content_policy === 'metadata' ? 'withheld' : '–')}
                  </TableCell>
                  <TableCell className="max-w-[20ch] truncate" title={s.target ?? ''}>
                    {s.target ?? '–'}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{num(s.input_tokens + s.output_tokens)}</TableCell>
                  <TableCell className="pr-6">
                    {s.trace_id && (
                      <Button variant="link" size="sm" className="h-auto p-0" asChild>
                        <Link to={`/traces/${s.trace_id}`}>View trace</Link>
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {total > PAGE_SIZE && (
            <div className="flex items-center gap-2 border-t px-6 py-3">
              <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <span className="text-xs text-muted-foreground">
                {page * PAGE_SIZE + 1}–{Math.min(total, (page + 1) * PAGE_SIZE)} of {total}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={(page + 1) * PAGE_SIZE >= total}
                onClick={() => setPage(page + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
