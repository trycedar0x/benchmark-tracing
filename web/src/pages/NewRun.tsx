import { useMutation, useQuery } from '@tanstack/react-query'
import { Play, Receipt } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { StatusBadge } from '@/components/status-badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { errorMessage, money, num } from '@/lib/format'
import { api, type Quote } from '../api'

const MOCK = 'mock/'

export default function NewRunPage() {
  const navigate = useNavigate()
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const models = useQuery({ queryKey: ['models'], queryFn: api.models })

  const [benchmark, setBenchmark] = useState('toy-arith@1')
  const [modelText, setModelText] = useState('mock/strong\nmock/weak')
  const [limit, setLimit] = useState('')
  const [epochs, setEpochs] = useState('1')
  const [content, setContent] = useState('full')
  const [cap, setCap] = useState('')
  const [quoteId, setQuoteId] = useState<string | null>(null)

  const modelList = useMemo(
    () =>
      modelText
        .split(/[\n,]/)
        .map((m) => m.trim())
        .filter(Boolean),
    [modelText],
  )
  const allMock = modelList.length > 0 && modelList.every((m) => m.startsWith(MOCK))
  const entry = catalog.data?.find((e) => e.ref === benchmark)

  const quote = useQuery({
    queryKey: ['quote', quoteId],
    queryFn: () => api.quote(quoteId!),
    enabled: !!quoteId,
    refetchInterval: (q) => (q.state.data?.status === 'estimating' ? 1000 : false),
  })

  const createQuote = useMutation({
    mutationFn: () =>
      api.createQuote({ benchmark, models: modelList, limit: limit ? Number(limit) : null, sample_size: 5 }),
    onSuccess: (q) => setQuoteId(q.id),
  })

  const startDirect = useMutation({
    mutationFn: () =>
      api.startRuns({
        benchmark,
        models: modelList,
        limit: limit ? Number(limit) : null,
        epochs: Number(epochs) || 1,
        content_policy: content,
        budget_usd: cap ? Number(cap) : null,
      }),
    onSuccess: () => navigate('/'),
  })

  const approveAndRun = useMutation({
    mutationFn: async () => {
      await api.approveQuote(quoteId!, cap ? Number(cap) : null)
      return api.startRuns({ quote_id: quoteId, content_policy: content, epochs: Number(epochs) || 1 })
    },
    onSuccess: () => navigate('/'),
  })

  const error = createQuote.error || startDirect.error || approveAndRun.error
  const resetQuote = () => setQuoteId(null)

  return (
    <div className="grid gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">New run</h1>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Plan</CardTitle>
            <CardDescription>Pick a benchmark variant and the models to evaluate.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={(e) => e.preventDefault()}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="benchmark">Benchmark</FieldLabel>
                  <Select
                    value={benchmark}
                    onValueChange={(v) => {
                      setBenchmark(v)
                      resetQuote()
                    }}
                  >
                    <SelectTrigger id="benchmark" className="w-full">
                      <SelectValue placeholder="Choose a benchmark" />
                    </SelectTrigger>
                    <SelectContent>
                      {catalog.data?.map((e) => (
                        <SelectItem key={e.ref} value={e.ref}>
                          {e.ref} · {e.family}
                          {e.offline ? ' (offline)' : ''}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {entry && (
                    <FieldDescription>
                      {entry.variant}. {num(entry.task_count)} tasks, graded by {entry.grader}.
                      {entry.sandbox ? ' Needs Docker.' : ''}
                    </FieldDescription>
                  )}
                </Field>
                <Field>
                  <FieldLabel htmlFor="models">Models, one per line</FieldLabel>
                  <Textarea
                    id="models"
                    className="font-mono"
                    rows={4}
                    value={modelText}
                    onChange={(e) => {
                      setModelText(e.target.value)
                      resetQuote()
                    }}
                    placeholder="openai/gpt-4o-mini"
                  />
                  <FieldDescription>
                    Inspect model names. Priced models: {models.data?.map((m) => m.model).join(', ')}
                  </FieldDescription>
                </Field>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Field>
                    <FieldLabel htmlFor="limit">Limit</FieldLabel>
                    <Input
                      id="limit"
                      inputMode="numeric"
                      placeholder="all"
                      value={limit}
                      onChange={(e) => {
                        setLimit(e.target.value)
                        resetQuote()
                      }}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="epochs">Epochs</FieldLabel>
                    <Input id="epochs" inputMode="numeric" value={epochs} onChange={(e) => setEpochs(e.target.value)} />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="content">Content</FieldLabel>
                    <Select value={content} onValueChange={setContent}>
                      <SelectTrigger id="content" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="full">Full</SelectItem>
                        <SelectItem value="redacted">Redacted</SelectItem>
                        <SelectItem value="metadata">Metadata only</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="cap">Budget cap (USD)</FieldLabel>
                    <Input
                      id="cap"
                      inputMode="decimal"
                      placeholder="none"
                      value={cap}
                      onChange={(e) => setCap(e.target.value)}
                    />
                  </Field>
                </div>
                {error && (
                  <Alert variant="destructive">
                    <AlertDescription>{errorMessage(error)}</AlertDescription>
                  </Alert>
                )}
              </FieldGroup>
            </form>
          </CardContent>
          <CardFooter className="flex flex-col items-start gap-3">
            <div className="flex flex-wrap gap-2">
              <Button disabled={!modelList.length || createQuote.isPending} onClick={() => createQuote.mutate()}>
                <Receipt /> Get quote
              </Button>
              {allMock && (
                <Button variant="outline" disabled={startDirect.isPending} onClick={() => startDirect.mutate()}>
                  <Play /> Run now (mock models)
                </Button>
              )}
            </div>
            <p className="text-sm text-muted-foreground">
              A quote runs 5 samples per model and extrapolates tokens and cost. With paid models that sample is billed.
              Paid models need an approved quote.
            </p>
          </CardFooter>
        </Card>
        <QuoteCard
          quote={quote.data}
          cap={cap}
          onApprove={() => approveAndRun.mutate()}
          approving={approveAndRun.isPending}
        />
      </div>
    </div>
  )
}

function QuoteCard({
  quote,
  cap,
  onApprove,
  approving,
}: {
  quote?: Quote
  cap: string
  onApprove: () => void
  approving: boolean
}) {
  if (!quote) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Quote</CardTitle>
          <CardDescription>Get a quote to see estimated tokens and cost before running.</CardDescription>
        </CardHeader>
      </Card>
    )
  }
  const upper = Object.values(quote.estimate).reduce((sum, e) => sum + (e.cost_usd_range?.[1] ?? 0), 0)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Quote</CardTitle>
        <CardDescription className="font-mono">{quote.id}</CardDescription>
        <CardAction>
          <StatusBadge value={quote.status} />
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4">
        {quote.status === 'estimating' ? (
          <p className="text-sm text-muted-foreground">Running {quote.sample_size} samples per model…</p>
        ) : (
          <>
            <p className="text-sm">
              {quote.benchmark}, {num(quote.samples_planned)} samples planned per model.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">Tokens (est.)</TableHead>
                  <TableHead className="text-right">Cost (est.)</TableHead>
                  <TableHead className="text-right">95% range</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(quote.estimate).map(([model, e]) => (
                  <TableRow key={model}>
                    <TableCell className="font-mono text-xs">
                      {model}
                      {e.note && <div className="font-sans text-muted-foreground">{e.note}</div>}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {num(Math.round((e.input_tokens ?? 0) + (e.output_tokens ?? 0)))}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{money(e.cost_usd)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {e.cost_usd_range ? `${money(e.cost_usd_range[0])} – ${money(e.cost_usd_range[1])}` : '–'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="text-sm text-muted-foreground">
              Upper estimate <span className="font-medium text-foreground">{money(upper)}</span>. Cap{' '}
              <span className="font-medium text-foreground">{cap ? money(Number(cap)) : 'none'}</span>
              {cap ? ', split evenly across models' : ''}. Caps cover calls the runner reports; provider billing may
              differ.
            </p>
          </>
        )}
      </CardContent>
      {quote.status === 'draft' && (
        <CardFooter>
          <Button disabled={approving} onClick={onApprove}>
            Approve and run
          </Button>
        </CardFooter>
      )}
    </Card>
  )
}
