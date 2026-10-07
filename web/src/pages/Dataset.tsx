import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Download, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { errorMessage } from '@/lib/format'
import { api, type DatasetItem } from '../api'

function ItemReview({ item, datasetId }: { item: DatasetItem; datasetId: string }) {
  const qc = useQueryClient()
  const [reference, setReference] = useState(item.reference_output ?? '')
  const [split, setSplit] = useState(item.split)
  const [rights, setRights] = useState(item.usage_rights)
  useEffect(() => {
    setReference(item.reference_output ?? '')
    setSplit(item.split)
    setRights(item.usage_rights)
  }, [item])

  const save = useMutation({
    mutationFn: (status?: string) =>
      api.reviewItem(datasetId, item.id, { reference_output: reference, split, usage_rights: rights, status }),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['dataset', datasetId] })
      toast.success(`Item ${updated.id}: ${updated.status}`)
    },
  })
  const target = item.provenance?.target as string | undefined

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Item {item.id} <StatusBadge value={item.status} />
        </CardTitle>
        <CardDescription>
          From{' '}
          <Link className="underline underline-offset-4" to={`/traces/${item.source_trace_id}`}>
            {item.source}
          </Link>
          {item.historical_score !== null && ` · historical score ${item.historical_score}`}
        </CardDescription>
        <CardAction>
          <Badge variant="outline">{item.split}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <div className="min-w-0 space-y-1">
          <div className="text-xs font-medium text-muted-foreground">Input</div>
          <pre className="max-h-48 overflow-auto rounded-md border bg-muted/40 p-2 font-mono text-xs whitespace-pre-wrap">
            {item.input ?? 'Content was withheld by policy; this item cannot be approved.'}
          </pre>
          <div className="pt-2 text-xs font-medium text-muted-foreground">
            Recorded output (evidence, not a reference)
          </div>
          <pre className="max-h-48 overflow-auto rounded-md border bg-muted/40 p-2 font-mono text-xs whitespace-pre-wrap">
            {item.historical_output ?? '–'}
          </pre>
        </div>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor={`ref-${item.id}`}>Reference answer</FieldLabel>
            <Textarea id={`ref-${item.id}`} rows={4} value={reference} onChange={(e) => setReference(e.target.value)} />
            {target && reference !== target && (
              <Button
                variant="link"
                size="sm"
                className="h-auto justify-start p-0"
                onClick={() => setReference(target)}
              >
                Use benchmark target: {target}
              </Button>
            )}
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field>
              <FieldLabel htmlFor={`split-${item.id}`}>Split</FieldLabel>
              <Select value={split} onValueChange={setSplit}>
                <SelectTrigger id={`split-${item.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="unassigned">Unassigned</SelectItem>
                  <SelectItem value="dev">Dev</SelectItem>
                  <SelectItem value="test">Held-out test</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor={`rights-${item.id}`}>Usage rights</FieldLabel>
              <Select value={rights} onValueChange={setRights}>
                <SelectTrigger id={`rights-${item.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="own_data">Own data</SelectItem>
                  <SelectItem value="licensed">Licensed</SelectItem>
                  <SelectItem value="unknown">Unknown</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
          {save.error && (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(save.error)}</AlertDescription>
            </Alert>
          )}
        </FieldGroup>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button disabled={save.isPending} onClick={() => save.mutate('approved')}>
          <Check /> Approve
        </Button>
        <Button variant="outline" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
          Save draft
        </Button>
        <Button variant="ghost" disabled={save.isPending} onClick={() => save.mutate('rejected')}>
          <X /> Reject
        </Button>
      </CardFooter>
    </Card>
  )
}

export default function DatasetPage() {
  const { datasetId = '' } = useParams()
  const [filter, setFilter] = useState('draft')
  const ds = useQuery({ queryKey: ['dataset', datasetId], queryFn: () => api.dataset(datasetId) })

  if (ds.error)
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(ds.error)}</AlertDescription>
      </Alert>
    )
  if (!ds.data) return <Skeleton className="h-64 w-full" />
  const items = ds.data.items.filter((i) => filter === 'all' || i.status === filter)
  const approved = ds.data.items.filter((i) => i.status === 'approved').length

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <p className="font-mono text-xs text-muted-foreground">{ds.data.id}</p>
          <h1 className="text-2xl font-semibold tracking-tight">{ds.data.name}</h1>
          <p className="text-sm text-muted-foreground">
            {ds.data.items.length} items, {approved} approved. Only approved items are exported.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={filter}
            onValueChange={(v) => v && setFilter(v)}
          >
            {['draft', 'needs_content', 'approved', 'rejected', 'all'].map((f) => (
              <ToggleGroupItem key={f} value={f}>
                {f.replace('_', ' ')}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Button variant="outline" disabled={!approved} asChild={approved > 0}>
            {approved > 0 ? (
              <a href={`/api/datasets/${datasetId}/export`}>
                <Download /> Export JSONL
              </a>
            ) : (
              <span>
                <Download /> Export JSONL
              </span>
            )}
          </Button>
        </div>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No items with this status.</p>
      ) : (
        items.map((item) => <ItemReview key={item.id} item={item} datasetId={datasetId} />)
      )}
    </div>
  )
}
