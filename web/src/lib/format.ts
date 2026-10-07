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

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
