import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorMessage, num } from '@/lib/format'
import { api } from '../api'

export default function CatalogPage() {
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const families = new Set(catalog.data?.map((e) => e.family)).size
  return (
    <div className="grid gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Catalog</h1>
        <p className="text-sm text-muted-foreground">
          {catalog.data?.length ?? '…'} variants across {families || '…'} benchmark families. Each variant pins task,
          arguments and grader; runs on different variants are not compared directly.
        </p>
      </div>
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
                <TableHead>Variant</TableHead>
                <TableHead className="text-right">Tasks</TableHead>
                <TableHead>Grader</TableHead>
                <TableHead>Needs</TableHead>
                <TableHead className="pr-6">License</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {catalog.data?.map((e) => (
                <TableRow key={e.ref} className="align-top">
                  <TableCell className="pl-6">
                    <div className="font-mono text-xs font-medium">{e.ref}</div>
                    <div className="text-xs text-muted-foreground">{e.family}</div>
                  </TableCell>
                  <TableCell className="max-w-md whitespace-normal">
                    {e.variant}
                    {e.notes && <div className="text-xs text-muted-foreground">{e.notes}</div>}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{num(e.task_count)}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal">{e.grader}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {[...e.requires, ...(e.sandbox ? ['docker'] : []), ...(e.offline ? ['offline'] : [])].map((n) => (
                        <Badge key={n} variant="outline">
                          {n}
                        </Badge>
                      ))}
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
