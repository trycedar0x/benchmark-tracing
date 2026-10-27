import { useMutation, useQuery } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { errorMessage, num } from '@/lib/format'
import { api, type EvaluationSubmission, type Me } from '../api'

function identifiers(text: string) {
  return text
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean)
}

export default function NewRunPage({ me, workspacePending }: { me: Me; workspacePending: boolean }) {
  const navigate = useNavigate()
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const targets = useQuery({
    queryKey: ['evaluation-targets', me.workspace?.id ?? null],
    queryFn: api.evaluationTargets,
  })
  const [benchmark, setBenchmark] = useState('')
  const [targetId, setTargetId] = useState('')
  const [judgeText, setJudgeText] = useState('')
  const [extractor, setExtractor] = useState('')
  const [sampleText, setSampleText] = useState('')
  const [limit, setLimit] = useState('')
  const [timeout, setTimeout] = useState('40')
  const [traceTimeout, setTraceTimeout] = useState('10')
  const [gradingTimeout, setGradingTimeout] = useState('180')
  const [content, setContent] = useState<EvaluationSubmission['content_policy']>('redacted')
  const [consent, setConsent] = useState(false)

  const entry = catalog.data?.find((item) => item.id === benchmark)
  const target = targets.data?.find((item) => item.id === targetId)
  const judges = identifiers(judgeText)
  const sampleIds = identifiers(sampleText)
  const needsExtractor = !!entry?.requires_extractor
  const viewer = me.role === 'viewer'
  const loading = workspacePending || catalog.isPending || targets.isPending
  const loadError = catalog.error || targets.error
  const traceMissing = !!entry?.requires_trace && !target?.has_trace_source
  const judgeError =
    judges.length > 0 && (new Set(judges).size !== judges.length || (entry?.judge_requirement === 'one' && judges.length !== 1))
  const validNumbers =
    (!limit || (Number.isInteger(Number(limit)) && Number(limit) > 0)) &&
    [timeout, gradingTimeout].every((value) => value !== '' && Number.isFinite(Number(value)) && Number(value) > 0) &&
    traceTimeout !== '' &&
    Number.isFinite(Number(traceTimeout)) &&
    Number(traceTimeout) >= 0
  const canSubmit =
    !viewer &&
    !loading &&
    !loadError &&
    !!entry &&
    !!target &&
    !traceMissing &&
    judges.length > 0 &&
    !judgeError &&
    (!needsExtractor || !!extractor.trim()) &&
    new Set(sampleIds).size === sampleIds.length &&
    validNumbers &&
    consent

  const submit = useMutation({
    mutationFn: () =>
      api.startEvaluation({
        benchmark,
        target_id: targetId,
        judge_models: judges,
        ...(needsExtractor ? { extractor_model: extractor.trim() } : {}),
        ...(sampleIds.length ? { sample_ids: sampleIds } : {}),
        ...(limit ? { limit: Number(limit) } : {}),
        timeout: Number(timeout),
        trace_timeout: Number(traceTimeout),
        grading_timeout: Number(gradingTimeout),
        content_policy: content,
        allow_paid: true,
      }),
    onSuccess: (run) => navigate(`/runs/${run.id}`),
  })

  return (
    <div className="grid max-w-3xl gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">New run</h1>
      {viewer && (
        <Alert>
          <AlertDescription>
            Read-only access: viewers can inspect evaluation settings but cannot start runs. Ask a workspace owner for
            member access.
          </AlertDescription>
        </Alert>
      )}
      {loading && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading benchmarks and authorized targets…
        </p>
      )}
      {loadError && (
        <Alert variant="destructive">
          <AlertDescription>
            <p>Could not load evaluation settings: {errorMessage(loadError)}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void catalog.refetch()
                void targets.refetch()
              }}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {!targets.isPending && !targets.error && targets.data?.length === 0 && (
        <Alert>
          <AlertDescription>
            No authorized agent targets are configured for this workspace. Ask the operator to configure
            EVERYBENCH_TARGETS_FILE with a workspace-scoped target and, for BigFinance, an authorized trace source. URLs
            and credentials cannot be supplied here. Shared or reverse-proxied deployments must enable authentication.
          </AlertDescription>
        </Alert>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Evaluate your agent</CardTitle>
          <CardDescription>
            Experimental finance adapters, not official leaderboard parity. Your configured agent answers; evaluator
            models grade its responses.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (canSubmit && !submit.isPending) submit.mutate()
            }}
          >
            <fieldset disabled={viewer || loading || !!loadError || submit.isPending} className="min-w-0">
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="benchmark">Benchmark</FieldLabel>
                  <Select value={benchmark} onValueChange={setBenchmark}>
                    <SelectTrigger id="benchmark" className="w-full">
                      <SelectValue placeholder="Choose a benchmark" />
                    </SelectTrigger>
                    <SelectContent>
                      {catalog.data?.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name} · {num(item.count)} tasks
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {entry && (
                    <FieldDescription>
                      {entry.description} Grading: {entry.judge_requirement === 'one' ? 'one judge' : 'one or more judges'}
                      {entry.requires_extractor ? ' and an explicit extractor' : ''}.{' '}
                      {entry.requires_trace
                        ? 'Requires genuine complete visible trace evidence, not just a final answer.'
                        : 'Grades the submitted response.'}
                    </FieldDescription>
                  )}
                </Field>
                <Field>
                  <FieldLabel htmlFor="target">Configured agent target</FieldLabel>
                  <Select value={targetId} onValueChange={setTargetId}>
                    <SelectTrigger id="target" className="w-full">
                      <SelectValue placeholder="Choose an authorized target" />
                    </SelectTrigger>
                    <SelectContent>
                      {targets.data?.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name} · {item.version}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {target && (
                    <FieldDescription>
                      {target.origin} ·{' '}
                      {target.has_trace_source ? 'Authorized trace source available' : 'No authorized trace source'}
                    </FieldDescription>
                  )}
                  {traceMissing && (
                    <Alert variant="destructive">
                      <AlertDescription>
                        BigFinance requires an authorized trace source. Choose a target with a trace source or ask the
                        operator to configure one before running.
                      </AlertDescription>
                    </Alert>
                  )}
                </Field>
                <Field>
                  <FieldLabel htmlFor="judges">Judge model IDs</FieldLabel>
                  <Textarea
                    id="judges"
                    rows={3}
                    className="font-mono"
                    value={judgeText}
                    onChange={(event) => setJudgeText(event.target.value)}
                    placeholder="provider/model-id"
                    required
                  />
                  <FieldDescription>
                    Explicit provider/model IDs, one per line or comma-separated.{' '}
                    {needsExtractor
                      ? 'FinDeepIndicator requires exactly one judge.'
                      : 'Choose one or more distinct judges.'}
                  </FieldDescription>
                  {judgeError && (
                    <p role="alert" className="text-sm text-destructive">
                      Choose distinct judge IDs.{needsExtractor ? ' FinDeepIndicator requires exactly one judge.' : ''}
                    </p>
                  )}
                </Field>
                {needsExtractor && (
                  <Field>
                    <FieldLabel htmlFor="extractor">Extractor model ID</FieldLabel>
                    <Input
                      id="extractor"
                      className="font-mono"
                      value={extractor}
                      onChange={(event) => setExtractor(event.target.value)}
                      placeholder="provider/model-id"
                      required
                    />
                    <FieldDescription>
                      FinDeepIndicator requires an explicit extractor model in addition to its judge.
                    </FieldDescription>
                  </Field>
                )}
                <Field>
                  <FieldLabel htmlFor="sample-ids">Sample IDs (optional)</FieldLabel>
                  <Textarea
                    id="sample-ids"
                    rows={2}
                    value={sampleText}
                    onChange={(event) => setSampleText(event.target.value)}
                    placeholder="One ID per line or comma-separated; blank selects all"
                  />
                  {new Set(sampleIds).size !== sampleIds.length && (
                    <p role="alert" className="text-sm text-destructive">
                      Sample IDs must be distinct.
                    </p>
                  )}
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor="limit">Sample limit (optional)</FieldLabel>
                    <Input
                      id="limit"
                      type="number"
                      min="1"
                      step="1"
                      placeholder="All selected samples"
                      value={limit}
                      onChange={(event) => setLimit(event.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="content">Content policy</FieldLabel>
                    <Select
                      value={content}
                      onValueChange={(value) => setContent(value as EvaluationSubmission['content_policy'])}
                    >
                      <SelectTrigger id="content" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="redacted">Redacted</SelectItem>
                        <SelectItem value="metadata">Metadata only</SelectItem>
                        <SelectItem value="full">Full</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field>
                    <FieldLabel htmlFor="timeout">Agent timeout (seconds)</FieldLabel>
                    <Input
                      id="timeout"
                      type="number"
                      min="0.001"
                      step="any"
                      value={timeout}
                      onChange={(event) => setTimeout(event.target.value)}
                      required
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="trace-timeout">Trace timeout (seconds)</FieldLabel>
                    <Input
                      id="trace-timeout"
                      type="number"
                      min="0"
                      step="any"
                      value={traceTimeout}
                      onChange={(event) => setTraceTimeout(event.target.value)}
                      required
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="grading-timeout">Grading timeout (seconds)</FieldLabel>
                    <Input
                      id="grading-timeout"
                      type="number"
                      min="0.001"
                      step="any"
                      value={gradingTimeout}
                      onChange={(event) => setGradingTimeout(event.target.value)}
                      required
                    />
                  </Field>
                </div>
                <Alert>
                  <AlertDescription>
                    The target must implement strict NDJSON: a conversation record with conversationId, text delta
                    records, a bare done record, then clean EOF. Requests contain only{' '}
                    {JSON.stringify({ message: 'case input' })}; references and grading rubrics are never sent to the
                    agent.
                  </AlertDescription>
                </Alert>
                <Alert>
                  <AlertDescription>
                    Remote agent and evaluator costs are unknown and uncapped. No cost estimate, quote or budget cap is
                    provided. Submission may incur charges from your agent and evaluator providers.
                  </AlertDescription>
                </Alert>
                <Field orientation="horizontal">
                  <Checkbox
                    id="consent"
                    checked={consent}
                    onCheckedChange={(checked) => setConsent(checked === true)}
                  />
                  <FieldLabel htmlFor="consent">
                    I explicitly allow paid agent and evaluator calls and accept uncapped costs.
                  </FieldLabel>
                </Field>
                {submit.error && (
                  <Alert variant="destructive">
                    <AlertDescription>Could not start evaluation: {errorMessage(submit.error)}</AlertDescription>
                  </Alert>
                )}
                <Button type="submit" disabled={!canSubmit || submit.isPending}>
                  <Play /> {submit.isPending ? 'Submitting…' : 'Start evaluation'}
                </Button>
              </FieldGroup>
            </fieldset>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
