import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { duration } from '@/lib/format'
import type { Span } from './api'

function Value({ value }: { value: unknown }) {
  // Model and tool content is untrusted: always rendered as text, never as HTML.
  if (value === null || value === undefined) return <span className="text-muted-foreground">–</span>
  if (typeof value === 'string') return <pre className="whitespace-pre-wrap break-words font-mono text-xs">{value}</pre>
  if (Array.isArray(value) && value.every((m) => m && typeof m === 'object' && 'role' in m)) {
    return (
      <div className="grid gap-2">
        {value.map((m, i) => (
          <div key={i} className="rounded-md border bg-muted/40 p-2">
            <Badge variant="outline" className="mb-1 font-mono">
              {String(m.role)}
            </Badge>
            <pre className="whitespace-pre-wrap break-words font-mono text-xs">{String(m.text ?? '')}</pre>
            {m.tool_calls && (
              <pre className="mt-1 whitespace-pre-wrap font-mono text-xs text-muted-foreground">
                {JSON.stringify(m.tool_calls, null, 2)}
              </pre>
            )}
          </div>
        ))}
      </div>
    )
  }
  return <pre className="whitespace-pre-wrap break-words font-mono text-xs">{JSON.stringify(value, null, 2)}</pre>
}

export function SpanDetail({ span }: { span: Span | null }) {
  if (!span) return <p className="text-sm text-muted-foreground">Select a span to see its details.</p>
  const content = span.content ?? {}
  return (
    <div className="grid gap-4 text-sm">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Badge variant={span.status === 'error' ? 'destructive' : 'secondary'} className="font-mono">
            {span.kind}
          </Badge>
          <span className="font-mono text-xs text-muted-foreground">{span.span_id}</span>
        </div>
        <div className="break-all text-base font-semibold">{span.name}</div>
        <div className="text-xs text-muted-foreground">
          {duration(span.start_time, span.end_time)} · {span.status} · source {span.source}
        </div>
      </div>
      {Object.keys(span.attributes).length > 0 && (
        <>
          <Separator />
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
            {Object.entries(span.attributes).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="font-mono text-xs text-muted-foreground">{k}</dt>
                <dd className="break-all font-mono text-xs">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {span.content_state !== 'present' && span.content_state !== 'missing' && (
        <Alert>
          <AlertDescription>Content {span.content_state} by policy.</AlertDescription>
        </Alert>
      )}
      {Object.entries(content).map(([k, v]) =>
        v === null || (Array.isArray(v) && v.length === 0) ? null : (
          <div key={k} className="space-y-1">
            <Separator />
            <div className="pt-2 text-xs font-medium text-muted-foreground">{k}</div>
            <Value value={v} />
          </div>
        ),
      )}
    </div>
  )
}
