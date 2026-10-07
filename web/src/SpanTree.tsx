import { ChevronDown, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Span } from './api'

export type SpanNode = Span & { children: SpanNode[]; depth: number }

export function buildTree(spans: Span[]): SpanNode[] {
  const ids = new Set(spans.map((s) => s.span_id))
  const nodes = new Map<string, SpanNode>()
  for (const s of spans) nodes.set(s.span_id, { ...s, children: [], depth: 0 })
  const roots: SpanNode[] = []
  for (const node of nodes.values()) {
    const parent = node.parent_id && ids.has(node.parent_id) ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const order = (a: SpanNode, b: SpanNode) => (a.start_time ?? '').localeCompare(b.start_time ?? '')
  const walk = (list: SpanNode[], depth: number) => {
    list.sort(order)
    for (const n of list) {
      n.depth = depth
      walk(n.children, depth + 1)
    }
  }
  walk(roots, 0)
  return roots
}

export function flatten(roots: SpanNode[], collapsed: Set<string> = new Set()): SpanNode[] {
  const out: SpanNode[] = []
  const visit = (n: SpanNode) => {
    out.push(n)
    if (!collapsed.has(n.span_id)) n.children.forEach(visit)
  }
  roots.forEach(visit)
  return out
}

export function spanSummary(s: Span): string {
  const a = s.attributes
  const bits: string[] = []
  if (a['gen_ai.usage.input_tokens'] !== undefined)
    bits.push(`${a['gen_ai.usage.input_tokens']}→${a['gen_ai.usage.output_tokens']} tok`)
  if (a['score.value'] !== undefined) bits.push(`score ${a['score.value']}`)
  if (a['outcome'] !== undefined) bits.push(String(a['outcome']))
  return bits.join(' · ')
}

const PROMINENT = new Set(['model', 'tool', 'scorer', 'error', 'sandbox'])

export function SpanTree({
  spans,
  selected,
  onSelect,
  highlight,
}: {
  spans: Span[]
  selected?: string | null
  onSelect?: (span: Span) => void
  highlight?: Set<string>
}) {
  const roots = useMemo(() => buildTree(spans), [spans])
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const rows = flatten(roots, collapsed)
  const toggle = (id: string) =>
    setCollapsed((c) => {
      const next = new Set(c)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <ul className="text-sm" role="tree">
      {rows.map((n) => (
        <li
          key={n.span_id}
          role="treeitem"
          aria-selected={selected === n.span_id}
          className={cn(
            'flex min-w-0 cursor-pointer items-center gap-2 rounded-md py-1 pr-2',
            selected === n.span_id ? 'bg-accent text-accent-foreground' : 'hover:bg-muted',
            highlight?.has(n.span_id) && 'ring-1 ring-destructive',
          )}
          style={{ paddingLeft: `${n.depth * 16 + 4}px` }}
          onClick={() => onSelect?.(n)}
        >
          <button
            className="flex size-4 shrink-0 items-center justify-center text-muted-foreground"
            aria-label={collapsed.has(n.span_id) ? 'Expand' : 'Collapse'}
            onClick={(e) => {
              e.stopPropagation()
              if (n.children.length) toggle(n.span_id)
            }}
          >
            {n.children.length ? (
              collapsed.has(n.span_id) ? (
                <ChevronRight className="size-3.5" />
              ) : (
                <ChevronDown className="size-3.5" />
              )
            ) : null}
          </button>
          <Badge
            variant={
              n.status === 'error' || n.kind === 'error'
                ? 'destructive'
                : PROMINENT.has(n.kind)
                  ? 'secondary'
                  : 'outline'
            }
            className="shrink-0 font-mono"
          >
            {n.kind}
          </Badge>
          <span className="min-w-0 truncate font-mono text-xs">{n.name}</span>
          <span className="ml-auto shrink-0 pl-2 text-xs text-muted-foreground">{spanSummary(n)}</span>
          <span className="w-14 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
            {duration(n.start_time, n.end_time)}
          </span>
        </li>
      ))}
    </ul>
  )
}
