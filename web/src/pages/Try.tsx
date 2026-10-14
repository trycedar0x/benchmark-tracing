import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { api, type TryResult } from '@/api'

export default function TryPage() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<TryResult | null>(null)

  async function onTest() {
    setPending(true)
    setError('')
    try {
      setResult(await api.tryAgent())
    } catch {
      setError('Something went wrong. Try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <main
      className={
        result ? 'mx-auto max-w-2xl px-6 py-16' : 'mx-auto flex min-h-svh max-w-2xl flex-col justify-center px-6 py-16'
      }
    >
      <p className="text-3xl leading-snug tracking-tight">
        This is a stock-research assistant. It reads a question about a stock and says whether you should be more
        bullish.
      </p>
      <p className="mt-4 text-lg text-muted-foreground">These are practice questions, not today's news.</p>
      <div className="mt-8">
        <Button className="h-12 px-6 text-base" disabled={pending} onClick={() => void onTest()}>
          {pending ? 'Testing…' : 'Test this agent'}
        </Button>
      </div>
      {error && <p className="mt-6 text-base">{error}</p>}
      {result && (
        <section className="mt-12">
          <p className="text-2xl tracking-tight">{result.summary}</p>
          <ul className="mt-8 flex flex-col gap-8">
            {result.cases.map((item) => (
              <li key={item.question}>
                <p className="text-base font-medium">{item.question}</p>
                <p className="mt-1 text-base text-muted-foreground">{item.line}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  )
}
