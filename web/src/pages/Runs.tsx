import { useQuery } from '@tanstack/react-query'
import { FlaskConical, GitCompare, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ago, errorMessage, money, pct, who } from '@/lib/format'
import { api, TERMINAL } from '../api'

export default function RunsPage() {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<string[]>([])
  const runs = useQuery({
    queryKey: ['runs'],
    queryFn: () => api.runs(),
    refetchInterval: (q) => (q.state.data?.some((r) => !TERMINAL.has(r.status)) ? 1500 : 10000),
  })

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]))

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={selected.length !== 2}
            onClick={() => navigate(`/compare?a=${selected[0]}&b=${selected[1]}`)}
          >
            <GitCompare /> Compare selected ({selected.length}/2)
          </Button>
          <Button onClick={() => navigate('/new')}>
            <Plus /> New run
          </Button>
        </div>
      </div>
      {runs.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(runs.error)}</AlertDescription>
        </Alert>
      )}
      {runs.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : runs.data?.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FlaskConical />
            </EmptyMedia>
            <EmptyTitle>No runs yet</EmptyTitle>
            <EmptyDescription>
              Try toy-arith with mock/strong and mock/weak. It runs offline with no API keys.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button onClick={() => navigate('/new')}>Start a run</Button>
          </EmptyContent>
        </Empty>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>Run</TableHead>
                  <TableHead>Benchmark</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Progress</TableHead>
                  <TableHead className="text-right">Accuracy</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.data?.map((r) => (
                  <TableRow key={r.id} data-state={selected.includes(r.id) ? 'selected' : undefined}>
                    <TableCell>
                      <Checkbox
                        aria-label={`Select ${r.id} for comparison`}
                        checked={selected.includes(r.id)}
                        onCheckedChange={() => toggle(r.id)}
                      />
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      <Link className="underline-offset-4 hover:underline" to={`/runs/${r.id}`}>
                        {r.id}
                      </Link>
                    </TableCell>
                    <TableCell>{r.benchmark}</TableCell>
                    <TableCell className="font-mono text-xs">{who(r.model, r.agent)}</TableCell>
                    <TableCell>
                      <StatusBadge value={r.status} />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Progress
                          className="w-24"
                          value={r.samples_total ? (100 * r.samples_done) / r.samples_total : 0}
                        />
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {r.samples_done}/{r.samples_total ?? '?'}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{pct(r.accuracy)}</TableCell>
                    <TableCell className="text-right tabular-nums">{r.n_error}</TableCell>
                    <TableCell className="text-right tabular-nums">{money(r.cost_usd)}</TableCell>
                    <TableCell className="text-muted-foreground">{ago(r.created_at)}</TableCell>
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
