import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Span } from '../api'
import { SpanDetail } from '../SpanDetail'
import { SpanTree } from '../SpanTree'
import { Badge, Card, ErrorNote } from '../ui'

export default function TracePage() {
  const { traceId = '' } = useParams()
  const trace = useQuery({ queryKey: ['trace', traceId], queryFn: () => api.trace(traceId) })
  const [selected, setSelected] = useState<Span | null>(null)

  if (trace.error) return <ErrorNote error={trace.error} />
  if (!trace.data) return <div className="text-muted">Loading…</div>
  const { trace: header, sample, spans } = trace.data
  const current = selected ?? spans.find((s) => s.parent_id === null) ?? null

  return (
    <div className="grid gap-4">
      <div className="min-w-0">
        <div className="font-mono text-xs text-muted">trace {header.trace_id}</div>
        <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">
          {header.name ?? 'Trace'} {sample && <Badge value={sample.outcome} />}
        </h1>
        <div className="text-xs text-muted">
          {header.run_id && (
            <>
              run{' '}
              <Link className="text-accent hover:underline" to={`/runs/${header.run_id}`}>
                {header.run_id}
              </Link>{' '}
              ·{' '}
            </>
          )}
          {header.span_count} spans · source {header.source}
        </div>
      </div>
      {sample && (
        <Card>
          <div className="grid gap-3 text-[13px] md:grid-cols-3">
            <div className="min-w-0">
              <div className="font-mono text-[11px] text-muted uppercase">Input</div>
              <pre className="max-h-40 overflow-auto font-mono text-xs whitespace-pre-wrap">{sample.input ?? 'withheld'}</pre>
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[11px] text-muted uppercase">Answer / output</div>
              <pre className="max-h-40 overflow-auto font-mono text-xs whitespace-pre-wrap">{sample.error ?? sample.output ?? sample.answer ?? 'withheld'}</pre>
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[11px] text-muted uppercase">Target · grading</div>
              <pre className="max-h-40 overflow-auto font-mono text-xs whitespace-pre-wrap">
                {sample.target ?? '–'}
                {sample.explanation ? `\n\n${sample.explanation}` : ''}
              </pre>
            </div>
          </div>
        </Card>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <Card title="Spans">
          <SpanTree spans={spans} selected={current?.span_id} onSelect={setSelected} />
        </Card>
        <Card title="Detail" className="lg:sticky lg:top-16 lg:self-start">
          <SpanDetail span={current} />
        </Card>
      </div>
    </div>
  )
}
