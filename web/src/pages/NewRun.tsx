import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Quote } from '../api'
import { Badge, Button, Card, ErrorNote, inputCls, money, num, td, th } from '../ui'

const MOCK = 'btmock/'

export default function NewRunPage() {
  const navigate = useNavigate()
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const models = useQuery({ queryKey: ['models'], queryFn: api.models })

  const [benchmark, setBenchmark] = useState('toy-arith@1')
  const [modelText, setModelText] = useState('btmock/strong\nbtmock/weak')
  const [limit, setLimit] = useState('')
  const [epochs, setEpochs] = useState('1')
  const [content, setContent] = useState('full')
  const [cap, setCap] = useState('')
  const [quoteId, setQuoteId] = useState<string | null>(null)

  const modelList = useMemo(
    () => modelText.split(/[\n,]/).map((m) => m.trim()).filter(Boolean),
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

  return (
    <div className="grid gap-4">
      <h1 className="text-xl font-semibold">New run</h1>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Plan">
          <form className="grid gap-4" onSubmit={(e) => e.preventDefault()}>
            <label className="grid gap-1">
              <span className="text-xs font-semibold text-muted">Benchmark</span>
              <select id="benchmark" className={inputCls} value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
                {catalog.data?.map((e) => (
                  <option key={e.ref} value={e.ref}>
                    {e.ref} · {e.family}
                    {e.offline ? ' (offline)' : ''}
                  </option>
                ))}
              </select>
              {entry && (
                <span className="text-xs text-muted">
                  {entry.variant}. {num(entry.task_count)} tasks, graded by {entry.grader}.
                  {entry.sandbox ? ' Needs Docker.' : ''}
                </span>
              )}
            </label>
            <label className="grid gap-1">
              <span className="text-xs font-semibold text-muted">Models, one per line</span>
              <textarea
                id="models"
                className={`${inputCls} h-24 font-mono`}
                value={modelText}
                onChange={(e) => {
                  setModelText(e.target.value)
                  setQuoteId(null)
                }}
                placeholder="openai/gpt-4o-mini"
              />
              <span className="text-xs text-muted">
                Inspect model names, e.g. <code>openai/gpt-4o-mini</code>. Priced models:{' '}
                {models.data?.map((m) => m.model).join(', ')}
              </span>
            </label>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <label className="grid gap-1">
                <span className="text-xs font-semibold text-muted">Limit</span>
                <input id="limit" className={inputCls} inputMode="numeric" placeholder="all" value={limit} onChange={(e) => { setLimit(e.target.value); setQuoteId(null) }} />
              </label>
              <label className="grid gap-1">
                <span className="text-xs font-semibold text-muted">Epochs</span>
                <input id="epochs" className={inputCls} inputMode="numeric" value={epochs} onChange={(e) => setEpochs(e.target.value)} />
              </label>
              <label className="grid gap-1">
                <span className="text-xs font-semibold text-muted">Content</span>
                <select id="content" className={inputCls} value={content} onChange={(e) => setContent(e.target.value)}>
                  <option value="full">full</option>
                  <option value="redacted">redacted</option>
                  <option value="metadata">metadata only</option>
                </select>
              </label>
              <label className="grid gap-1">
                <span className="text-xs font-semibold text-muted">Budget cap (USD)</span>
                <input id="cap" className={inputCls} inputMode="decimal" placeholder="none" value={cap} onChange={(e) => setCap(e.target.value)} />
              </label>
            </div>
            <ErrorNote error={createQuote.error || startDirect.error || approveAndRun.error} />
            <div className="flex flex-wrap gap-2">
              <Button kind="primary" disabled={!modelList.length || createQuote.isPending} onClick={() => createQuote.mutate()}>
                Get quote
              </Button>
              {allMock && (
                <Button disabled={startDirect.isPending} onClick={() => startDirect.mutate()}>
                  Run now (mock models)
                </Button>
              )}
            </div>
            <p className="text-xs text-muted">
              A quote runs 5 samples per model and extrapolates tokens and cost. With paid models that sample run
              is billed. Paid models need an approved quote.
            </p>
          </form>
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

function QuoteCard({ quote, cap, onApprove, approving }: { quote?: Quote; cap: string; onApprove: () => void; approving: boolean }) {
  if (!quote) {
    return (
      <Card title="Quote">
        <p className="text-sm text-muted">Get a quote to see estimated tokens and cost before running.</p>
      </Card>
    )
  }
  const upper = Object.values(quote.estimate).reduce((sum, e) => sum + (e.cost_usd_range?.[1] ?? 0), 0)
  return (
    <Card title={<span className="flex items-center gap-2">Quote <span className="font-mono text-xs text-muted">{quote.id}</span></span>} actions={<Badge value={quote.status} />}>
      {quote.status === 'estimating' ? (
        <p className="text-sm text-muted">Running {quote.sample_size} samples per model…</p>
      ) : (
        <div className="grid gap-3">
          <p className="text-sm">
            {quote.benchmark}, {num(quote.samples_planned)} samples planned per model.
          </p>
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[480px] text-[13px]">
              <thead className="border-b border-line">
                <tr>
                  <th className={th}>Model</th>
                  <th className={`${th} text-right`}>Tokens (est.)</th>
                  <th className={`${th} text-right`}>Cost (est.)</th>
                  <th className={`${th} text-right`}>95% range</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(quote.estimate).map(([model, e]) => (
                  <tr key={model} className="border-b border-line last:border-0">
                    <td className={`${td} font-mono text-xs`}>
                      {model}
                      {e.note && <div className="font-sans text-muted">{e.note}</div>}
                    </td>
                    <td className={`${td} tabular text-right`}>{num(Math.round((e.input_tokens ?? 0) + (e.output_tokens ?? 0)))}</td>
                    <td className={`${td} tabular text-right`}>{money(e.cost_usd)}</td>
                    <td className={`${td} tabular text-right`}>
                      {e.cost_usd_range ? `${money(e.cost_usd_range[0])} – ${money(e.cost_usd_range[1])}` : '–'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-sm">
            Upper estimate: <strong>{money(upper)}</strong>. Cap: <strong>{cap ? money(Number(cap)) : 'none'}</strong>
            {cap ? ', split evenly across models.' : '.'} Caps cover calls the runner reports; provider billing may differ.
          </p>
          {quote.status === 'draft' && (
            <div>
              <Button kind="primary" disabled={approving} onClick={onApprove}>
                Approve and run
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
