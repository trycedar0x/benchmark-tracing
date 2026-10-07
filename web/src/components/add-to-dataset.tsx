import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ListPlus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { errorMessage } from '@/lib/format'
import { api } from '../api'

const NEW = '__new__'

export function AddToDataset({
  traceIds,
  runId,
  outcomes,
  label = 'Add to dataset',
}: {
  traceIds?: string[]
  runId?: string
  outcomes?: string[]
  label?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [datasetId, setDatasetId] = useState(NEW)
  const [name, setName] = useState('')
  const [split, setSplit] = useState('unassigned')
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: api.datasets, enabled: open })

  const add = useMutation({
    mutationFn: async () => {
      const id = datasetId === NEW ? (await api.createDataset({ name })).id : datasetId
      const items = await api.addItems(id, {
        trace_ids: traceIds ?? [],
        run_id: runId,
        outcomes: outcomes ?? [],
        split,
      })
      return { id, count: items.length }
    },
    onSuccess: ({ count }) => {
      qc.invalidateQueries({ queryKey: ['datasets'] })
      toast.success(`Added ${count} draft item${count === 1 ? '' : 's'}. Review them before use.`)
      setOpen(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <ListPlus /> {label}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>
            Items start as drafts. The recorded output is kept as evidence; a reviewer sets the reference answer, split
            and usage rights before approval.
          </DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="dataset">Dataset</FieldLabel>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger id="dataset" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NEW}>New dataset…</SelectItem>
                {datasets.data?.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {datasetId === NEW && (
            <Field>
              <FieldLabel htmlFor="dataset-name">Name</FieldLabel>
              <Input
                id="dataset-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Support failures"
              />
            </Field>
          )}
          <Field>
            <FieldLabel htmlFor="split">Split</FieldLabel>
            <Select value={split} onValueChange={setSplit}>
              <SelectTrigger id="split" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unassigned">Unassigned</SelectItem>
                <SelectItem value="dev">Dev</SelectItem>
                <SelectItem value="test">Held-out test</SelectItem>
              </SelectContent>
            </Select>
            <FieldDescription>Keep held-out test items out of prompt and config tuning.</FieldDescription>
          </Field>
          {add.error && (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(add.error)}</AlertDescription>
            </Alert>
          )}
        </FieldGroup>
        <DialogFooter>
          <Button disabled={add.isPending || (datasetId === NEW && !name.trim())} onClick={() => add.mutate()}>
            Add drafts
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
