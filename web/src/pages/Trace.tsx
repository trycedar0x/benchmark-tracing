import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage } from '@/lib/format'
import { api, type Span } from '../api'
import { SpanDetail } from '../SpanDetail'
import { SpanTree } from '../SpanTree'

export default function TracePage() {
  const { traceId = '' } = useParams()
  const trace = useQuery({ queryKey: ['trace', traceId], queryFn: () => api.trace(traceId) })
  const [selected, setSelected] = useState<Span | null>(null)

  if (trace.error)
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(trace.error)}</AlertDescription>
      </Alert>
    )
  if (!trace.data) return <Skeleton className="h-64 w-full" />
  const { trace: header, sample, spans } = trace.data
  const current = selected ?? spans.find((s) => s.parent_id === null) ?? null

  return (
    <div className="grid gap-4">
      <div className="min-w-0">
        <p className="font-mono text-xs text-muted-foreground">trace {header.trace_id}</p>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          {header.name ?? 'Trace'} {sample && <StatusBadge value={sample.outcome} />}
        </h1>
        <p className="text-sm text-muted-foreground">
          {header.run_id && (
            <>
              Run{' '}
              <Link className="underline underline-offset-4" to={`/runs/${header.run_id}`}>
                {header.run_id}
              </Link>{' '}
              ·{' '}
            </>
          )}
          {header.span_count} spans · source {header.source}
        </p>
      </div>
      {sample && (
        <Card>
          <CardContent className="grid gap-4 md:grid-cols-3">
            {[
              ['Input', sample.input ?? 'withheld'],
              ['Answer / output', sample.error ?? sample.output ?? sample.answer ?? 'withheld'],
              ['Target · grading', `${sample.target ?? '–'}${sample.explanation ? `\n\n${sample.explanation}` : ''}`],
            ].map(([label, text]) => (
              <div key={label} className="min-w-0 space-y-1">
                <div className="text-xs font-medium text-muted-foreground">{label}</div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs">{text}</pre>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Spans</CardTitle>
          </CardHeader>
          <CardContent>
            <SpanTree spans={spans} selected={current?.span_id} onSelect={setSelected} />
          </CardContent>
        </Card>
        <Card className="lg:sticky lg:top-16 lg:self-start">
          <CardHeader>
            <CardTitle>Detail</CardTitle>
          </CardHeader>
          <CardContent>
            <SpanDetail span={current} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
