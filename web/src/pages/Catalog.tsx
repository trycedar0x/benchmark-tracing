import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorMessage, num } from '@/lib/format'
import { api } from '../api'

export default function CatalogPage() {
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  return (
    <div className="grid gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Catalog</h1>
        <p className="text-sm text-muted-foreground">
          Experimental finance adapters for user-owned agents, not official leaderboard parity. FinDeepIndicator grades
          submitted responses (3,350 tasks); BigFinance supports only the public 50-item subset and requires genuine
          complete visible trace evidence.
        </p>
      </div>
      {catalog.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading benchmark catalog…
        </p>
      )}
      {catalog.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(catalog.error)}</AlertDescription>
        </Alert>
      )}
      <Card className="py-0">
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">Benchmark</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="text-right">Tasks</TableHead>
                <TableHead>Grader</TableHead>
                <TableHead>Requirements / status</TableHead>
                <TableHead className="pr-6">License</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {catalog.data?.map((e) => (
                <TableRow key={e.ref} className="align-top">
                  <TableCell className="pl-6">
                    <div className="font-medium">{e.name}</div>
                    <div className="font-mono text-xs text-muted-foreground">
                      {e.ref} · {e.family}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-md whitespace-normal">{e.description}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(e.task_count)}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal">{e.grader}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {e.experimental && <Badge variant="outline">Experimental</Badge>}
                      <Badge variant="outline">
                        {e.requires_trace ? 'Complete visible trace required' : 'Submitted response'}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell className="pr-6 text-xs">{e.license ?? '–'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
