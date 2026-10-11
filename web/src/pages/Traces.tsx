import { useQuery } from '@tanstack/react-query'
import { Waypoints } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ago, duration, errorMessage } from '@/lib/format'
import { api } from '../api'

const SOURCES = [
  'all',
  'sdk',
  'otlp',
  'inspect',
  'import:langfuse',
  'import:langsmith',
  'import:braintrust',
  'import:otlp_file',
]

export default function TracesPage() {
  const [params, setParams] = useSearchParams()
  const [source, setSourceState] = useState(params.get('source') ?? 'all')
  const setSource = (v: string) => {
    setSourceState(v)
    setParams(v === 'all' ? {} : { source: v })
  }
  const traces = useQuery({
    queryKey: ['traces', source],
    queryFn: () => api.traces(source === 'all' ? {} : { source }),
    refetchInterval: 5000,
  })

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Traces</h1>
          <p className="text-sm text-muted-foreground">
            Traces from your own agents (SDK or any OTLP exporter) and from benchmark runs.
          </p>
        </div>
        <Select value={source} onValueChange={setSource}>
          <SelectTrigger aria-label="Source" className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SOURCES.map((s) => (
              <SelectItem key={s} value={s}>
                {s === 'all' ? 'All sources' : s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {traces.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(traces.error)}</AlertDescription>
        </Alert>
      )}
      {traces.data?.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Waypoints />
            </EmptyMedia>
            <EmptyTitle>No traces from this source</EmptyTitle>
            <EmptyDescription>
              Send traces with <code>everyeval.sdk.EveryEval</code> or any OTLP/HTTP exporter pointed at{' '}
              <code>/v1/traces</code>.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">Trace</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="text-right">Spans</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead className="pr-6">Received</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {traces.data?.map((t) => (
                  <TableRow key={t.trace_id}>
                    <TableCell className="pl-6 font-mono text-xs">
                      <Link className="underline-offset-4 hover:underline" to={`/traces/${t.trace_id}`}>
                        {t.trace_id.slice(0, 16)}
                      </Link>
                    </TableCell>
                    <TableCell>{t.name ?? '–'}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{t.source}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          t.state === 'incomplete' ? 'destructive' : t.state === 'closed' ? 'secondary' : 'outline'
                        }
                      >
                        {t.state}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{t.span_count}</TableCell>
                    <TableCell className="text-right tabular-nums">{duration(t.start_time, t.end_time)}</TableCell>
                    <TableCell className="pr-6 text-muted-foreground">{ago(t.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
