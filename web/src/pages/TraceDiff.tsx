import { useQuery } from '@tanstack/react-query'
import { GitBranch } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorMessage } from '@/lib/format'
import { api, type DiffStep } from '../api'

const OP_LABEL = { same: 'same', changed: 'changed', only_a: 'only in A', only_b: 'only in B' } as const

function StepOutput({ step, label }: { step: DiffStep | null; label: string }) {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{label}</CardTitle>
        <CardDescription>{step ? `${step.kind} ${step.name}` : 'No matching step'}</CardDescription>
      </CardHeader>
      {step && (
        <CardContent>
          {/* Untrusted model and tool output: rendered as text only. */}
          <pre className="whitespace-pre-wrap break-words font-mono text-xs">{JSON.stringify(step.output, null, 2)}</pre>
        </CardContent>
      )}
    </Card>
  )
}

export default function TraceDiffPage() {
  const [params] = useSearchParams()
  const a = params.get('a') ?? ''
  const b = params.get('b') ?? ''
  const diff = useQuery({ queryKey: ['trace-diff', a, b], queryFn: () => api.traceDiff(a, b), enabled: !!a && !!b })
  const [selected, setSelected] = useState<number | null>(null)

  if (diff.error)
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(diff.error)}</AlertDescription>
      </Alert>
    )
  if (!diff.data) return <Skeleton className="h-64 w-full" />
  const d = diff.data
  const current = selected ?? d.first_divergence
  const pair = current !== null ? d.pairs[current] : null

  return (
    <div className="grid gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Trace diff · {d.a.name}</h1>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span className="flex items-center gap-2">
            A{' '}
            <Link className="font-mono underline underline-offset-4" to={`/traces/${d.a.trace_id}`}>
              {d.a.run_id}
            </Link>
            {d.a.outcome && <StatusBadge value={d.a.outcome} />}
          </span>
          <span className="flex items-center gap-2">
            B{' '}
            <Link className="font-mono underline underline-offset-4" to={`/traces/${d.b.trace_id}`}>
              {d.b.run_id}
            </Link>
            {d.b.outcome && <StatusBadge value={d.b.outcome} />}
          </span>
        </div>
      </div>
      <Alert>
        <GitBranch />
        <AlertTitle>{d.first_divergence === null ? 'No divergence' : 'First divergence'}</AlertTitle>
        <AlertDescription>{d.summary}</AlertDescription>
      </Alert>
      <Card className="py-0">
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12 pl-6">#</TableHead>
                <TableHead>Step</TableHead>
                <TableHead>A</TableHead>
                <TableHead>B</TableHead>
                <TableHead className="pr-6">Result</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {d.pairs.map((p, i) => {
                const step = p.a ?? p.b!
                return (
                  <TableRow
                    key={i}
                    data-state={current === i ? 'selected' : undefined}
                    className="cursor-pointer"
                    onClick={() => setSelected(i)}
                  >
                    <TableCell className="pl-6 tabular-nums">
                      {i + 1}
                      {d.first_divergence === i && <span className="sr-only"> first divergence</span>}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Badge variant={step.kind === 'error' ? 'destructive' : 'secondary'} className="font-mono">
                          {step.kind}
                        </Badge>
                        <span className="font-mono text-xs">{step.name}</span>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[36ch] truncate font-mono text-xs" title={p.a?.summary}>
                      {p.a?.summary ?? '–'}
                    </TableCell>
                    <TableCell className="max-w-[36ch] truncate font-mono text-xs" title={p.b?.summary}>
                      {p.b?.summary ?? '–'}
                    </TableCell>
                    <TableCell className="pr-6">
                      <div className="flex flex-wrap items-center gap-1">
                        <Badge variant={p.op === 'same' ? 'outline' : p.op === 'changed' ? 'secondary' : 'destructive'}>
                          {OP_LABEL[p.op]}
                        </Badge>
                        {p.differences.map((x) => (
                          <Badge key={x} variant="outline" className="font-mono">
                            {x}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      {pair && (
        <div className="grid gap-4 md:grid-cols-2">
          <StepOutput step={pair.a} label={`A · step ${current! + 1}`} />
          <StepOutput step={pair.b} label={`B · step ${current! + 1}`} />
        </div>
      )}
      <div>
        <Button variant="outline" asChild>
          <Link to={`/compare?a=${d.a.run_id}&b=${d.b.run_id}`}>Back to comparison</Link>
        </Button>
      </div>
    </div>
  )
}
