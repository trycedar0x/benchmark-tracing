import type { Span } from './api'
import { duration } from './ui'

function Value({ value }: { value: unknown }) {
  // Model and tool content is untrusted: always rendered as text, never as HTML.
  if (value === null || value === undefined) return <span className="text-muted">–</span>
  if (typeof value === 'string') return <pre className="font-mono text-xs whitespace-pre-wrap break-words">{value}</pre>
  if (Array.isArray(value) && value.every((m) => m && typeof m === 'object' && 'role' in m)) {
    return (
      <div className="grid gap-2">
        {value.map((m, i) => (
          <div key={i} className="rounded border border-line bg-sunken p-2">
            <div className="mb-1 font-mono text-[11px] font-semibold text-muted uppercase">{String(m.role)}</div>
            <pre className="font-mono text-xs whitespace-pre-wrap break-words">{String(m.text ?? '')}</pre>
            {m.tool_calls && (
              <pre className="mt-1 font-mono text-xs text-tool whitespace-pre-wrap">{JSON.stringify(m.tool_calls, null, 2)}</pre>
            )}
          </div>
        ))}
      </div>
    )
  }
  return <pre className="font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(value, null, 2)}</pre>
}

export function SpanDetail({ span }: { span: Span | null }) {
  if (!span) return <p className="text-sm text-muted">Select a span to see its details.</p>
  const content = span.content ?? {}
  return (
    <div className="grid gap-4 text-[13px]">
      <div>
        <div className="font-mono text-xs text-muted">
          {span.kind} · {span.span_id}
        </div>
        <div className="text-base font-semibold break-all">{span.name}</div>
        <div className="text-xs text-muted">
          {duration(span.start_time, span.end_time)} · {span.status} · source {span.source}
        </div>
      </div>
      {Object.keys(span.attributes).length > 0 && (
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
          {Object.entries(span.attributes).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-xs text-muted">{k}</dt>
              <dd className="font-mono text-xs break-all">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {span.content_state !== 'present' && span.content_state !== 'missing' && (
        <div className="rounded bg-sunken px-2 py-1 text-xs text-muted">Content {span.content_state} by policy.</div>
      )}
      {Object.entries(content).map(([k, v]) =>
        v === null || (Array.isArray(v) && v.length === 0) ? null : (
          <div key={k}>
            <div className="mb-1 font-mono text-[11px] font-semibold tracking-wide text-muted uppercase">{k}</div>
            <Value value={v} />
          </div>
        ),
      )}
    </div>
  )
}
