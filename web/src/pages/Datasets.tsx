import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ago, errorMessage } from '@/lib/format'
import { api } from '../api'

export default function DatasetsPage() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: api.datasets })
  const create = useMutation({
    mutationFn: () => api.createDataset({ name }),
    onSuccess: () => {
      setName('')
      qc.invalidateQueries({ queryKey: ['datasets'] })
    },
  })

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Datasets</h1>
          <p className="text-sm text-muted-foreground">
            Draft tasks from traces, review them, and export approved items as Inspect-compatible JSONL.
          </p>
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) create.mutate()
          }}
        >
          <Input
            aria-label="New dataset name"
            placeholder="New dataset name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={!name.trim() || create.isPending}>
            <Plus /> Create
          </Button>
        </form>
      </div>
      {(datasets.error || create.error) && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(datasets.error || create.error)}</AlertDescription>
        </Alert>
      )}
      {datasets.data?.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Database />
            </EmptyMedia>
            <EmptyTitle>No datasets yet</EmptyTitle>
            <EmptyDescription>
              Use “Add to dataset” on a trace or on a run’s failures, or create an empty dataset here.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">Dataset</TableHead>
                  <TableHead>Items</TableHead>
                  <TableHead className="pr-6">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {datasets.data?.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="pl-6">
                      <Link className="font-medium underline-offset-4 hover:underline" to={`/datasets/${d.id}`}>
                        {d.name}
                      </Link>
                      <div className="font-mono text-xs text-muted-foreground">{d.id}</div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(d.counts ?? {}).map(([status, count]) => (
                          <Badge key={status} variant={status === 'approved' ? 'secondary' : 'outline'}>
                            {count} {status.replace('_', ' ')}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="pr-6 text-muted-foreground">{ago(d.created_at)}</TableCell>
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
