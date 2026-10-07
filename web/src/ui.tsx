import type { ReactNode } from 'react'

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return '–'
  return value < 1 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? '–' : `${(value * 100).toFixed(digits)}%`
}

export function num(value: number | null | undefined): string {
  return value === null || value === undefined ? '–' : value.toLocaleString()
}

export function ago(iso: string | null): string {
  if (!iso) return '–'
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}

export function duration(start: string | null, end: string | null): string {
  if (!start || !end) return ''
  const ms = new Date(end).getTime() - new Date(start).getTime()
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`
}

const STATUS_STYLE: Record<string, string> = {
  succeeded: 'bg-good-bg text-good',
  correct: 'bg-good-bg text-good',
  improvement: 'bg-good-bg text-good',
  approved: 'bg-good-bg text-good',
  failed: 'bg-bad-bg text-bad',
  error: 'bg-bad-bg text-bad',
  incorrect: 'bg-bad-bg text-bad',
  regression: 'bg-bad-bg text-bad',
  budget_exceeded: 'bg-warn-bg text-warn',
  cancelled: 'bg-warn-bg text-warn',
  cancelling: 'bg-warn-bg text-warn',
  partial: 'bg-warn-bg text-warn',
  running: 'bg-info-bg text-accent',
  estimating: 'bg-info-bg text-accent',
  queued: 'bg-sunken text-muted',
}

export function Badge({ value }: { value: string }) {
  const style = STATUS_STYLE[value] ?? 'bg-sunken text-muted'
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold whitespace-nowrap ${style}`}>
      {value.replace('_', ' ')}
    </span>
  )
}

export function Card({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-md border border-line bg-surface ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-[13px] font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[11px] tracking-wide text-muted uppercase">{label}</div>
      <div className="tabular truncate text-lg font-semibold">{value}</div>
      {sub && <div className="truncate text-xs text-muted">{sub}</div>}
    </div>
  )
}

export function Button({
  children,
  onClick,
  kind = 'secondary',
  disabled,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  kind?: 'primary' | 'secondary' | 'danger'
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  const style = {
    primary: 'bg-accent text-accent-fg border-accent hover:opacity-90',
    secondary: 'bg-surface text-fg border-line hover:bg-sunken',
    danger: 'bg-surface text-bad border-line hover:bg-bad-bg',
  }[kind]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded border px-3 py-1.5 text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50 ${style}`}
    >
      {children}
    </button>
  )
}

export function Progress({ done, total }: { done: number; total: number | null }) {
  const ratio = total ? Math.min(1, done / total) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded bg-sunken">
        <div className="h-full bg-accent" style={{ width: `${ratio * 100}%` }} />
      </div>
      <span className="tabular text-xs text-muted">
        {done}/{total ?? '?'}
      </span>
    </div>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return <div className="rounded border border-bad/40 bg-bad-bg px-3 py-2 text-sm text-bad">{message}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-muted">{children}</div>
}

export const th = 'px-3 py-2 text-left font-mono text-[11px] font-semibold tracking-wide text-muted uppercase whitespace-nowrap'
export const td = 'px-3 py-2 align-top'
export const inputCls =
  'w-full rounded border border-line bg-surface px-2.5 py-1.5 text-[13px] text-fg placeholder:text-muted focus:border-accent focus:outline-none'
