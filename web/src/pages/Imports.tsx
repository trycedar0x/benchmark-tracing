import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ago, errorMessage } from '@/lib/format'
import { api } from '../api'

const SOURCES = {
  langfuse: { label: 'Langfuse', env: 'LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST', project: false },
  langsmith: { label: 'LangSmith', env: 'LANGSMITH_API_KEY, LANGSMITH_ENDPOINT', project: true },
  braintrust: { label: 'Braintrust', env: 'BRAINTRUST_API_KEY', project: true },
} as const
type Source = keyof typeof SOURCES

export default function ImportsPage() {
  const qc = useQueryClient()
  const [source, setSource] = useState<Source>('langfuse')
  const [project, setProject] = useState('')
  const [maxTraces, setMaxTraces] = useState('100')
  const [rights, setRights] = useState('')
  const [content, setContent] = useState('redacted')
  const imports = useQuery({
    queryKey: ['imports'],
    queryFn: api.imports,
    refetchInterval: (q) => (q.state.data?.some((i) => ['queued', 'running'].includes(i.status)) ? 1500 : false),
  })
  const start = useMutation({
    mutationFn: () =>
      api.startImport({
        source,
        project: project || null,
        max_traces: Number(maxTraces) || 100,
        usage_rights: rights,
        content_policy: content,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['imports'] }),
  })
  const meta = SOURCES[source]

  return (
    <div className="grid gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Imports</h1>
        <p className="text-sm text-muted-foreground">
          Bring production traces from other tools, then draft evaluation datasets from them. Files and Inspect logs
          import with the CLI: <code>benchtrace import otlp</code> and <code>benchtrace import inspect-log</code>.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card>
          <CardHeader>
            <CardTitle>New import</CardTitle>
            <CardDescription>Credentials are read from the server environment: {meta.env}.</CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="source">Source</FieldLabel>
                <Select value={source} onValueChange={(v) => setSource(v as Source)}>
                  <SelectTrigger id="source" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(SOURCES).map(([key, s]) => (
                      <SelectItem key={key} value={key}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              {meta.project && (
                <Field>
                  <FieldLabel htmlFor="project">Project</FieldLabel>
                  <Input id="project" value={project} onChange={(e) => setProject(e.target.value)} />
                </Field>
              )}
              <div className="grid grid-cols-2 gap-4">
                <Field>
                  <FieldLabel htmlFor="max-traces">Most recent traces</FieldLabel>
                  <Input
                    id="max-traces"
                    inputMode="numeric"
                    value={maxTraces}
                    onChange={(e) => setMaxTraces(e.target.value)}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="import-content">Content</FieldLabel>
                  <Select value={content} onValueChange={setContent}>
                    <SelectTrigger id="import-content" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="full">Full</SelectItem>
                      <SelectItem value="redacted">Redacted</SelectItem>
                      <SelectItem value="metadata">Metadata only</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              <Field>
                <FieldLabel htmlFor="rights">Usage rights</FieldLabel>
                <Select value={rights} onValueChange={setRights}>
                  <SelectTrigger id="rights" className="w-full">
                    <SelectValue placeholder="Declare your right to use this data" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="own_data">Our own data</SelectItem>
                    <SelectItem value="licensed">Licensed for evaluation use</SelectItem>
                    <SelectItem value="unknown">Unknown (items cannot be approved)</SelectItem>
                  </SelectContent>
                </Select>
                <FieldDescription>Imported data may contain personal or customer information.</FieldDescription>
              </Field>
              {start.error && (
                <Alert variant="destructive">
                  <AlertDescription>{errorMessage(start.error)}</AlertDescription>
                </Alert>
              )}
            </FieldGroup>
          </CardContent>
          <CardFooter>
            <Button disabled={!rights || start.isPending || (meta.project && !project)} onClick={() => start.mutate()}>
              <Download /> Import traces
            </Button>
          </CardFooter>
        </Card>
        <Card className="pb-0">
          <CardHeader>
            <CardTitle>History</CardTitle>
          </CardHeader>
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Traces</TableHead>
                  <TableHead>Rights</TableHead>
                  <TableHead className="pr-6">Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {imports.data?.map((i) => (
                  <TableRow key={i.id}>
                    <TableCell className="pl-6">
                      <div>{i.source}</div>
                      <div className="text-xs text-muted-foreground">{i.project ?? ''}</div>
                    </TableCell>
                    <TableCell className="max-w-xs whitespace-normal">
                      <StatusBadge value={i.status} />
                      {i.error && <div className="mt-1 text-xs text-destructive">{i.error}</div>}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {i.status === 'succeeded' ? (
                        <Link className="underline underline-offset-4" to={`/traces?source=import:${i.source}`}>
                          {i.traces_imported}
                        </Link>
                      ) : (
                        i.traces_imported
                      )}
                    </TableCell>
                    <TableCell className="text-xs">{i.usage_rights}</TableCell>
                    <TableCell className="pr-6 text-muted-foreground">{ago(i.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
