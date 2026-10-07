import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, TERMINAL } from '../api'
import { Badge, Button, Card, Empty, ErrorNote, Progress, ago, money, pct, td, th } from '../ui'

export default function RunsPage() {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<string[]>([])
  const runs = useQuery({
    queryKey: ['runs'],
    queryFn: () => api.runs(),
    refetchInterval: (q) => (q.state.data?.some((r) => !TERMINAL.has(r.status)) ? 1500 : 10000),
  })

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]))

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Runs</h1>
        <div className="flex gap-2">
          <Button
            disabled={selected.length !== 2}
            onClick={() => navigate(`/compare?a=${selected[0]}&b=${selected[1]}`)}
          >
            Compare selected ({selected.length}/2)
          </Button>
          <Button kind="primary" onClick={() => navigate('/new')}>
            New run
          </Button>
        </div>
      </div>
      <ErrorNote error={runs.error} />
      <Card>
        {runs.data && runs.data.length === 0 ? (
          <Empty>
            No runs yet. <Link className="text-accent underline" to="/new">Start one</Link>, e.g. toy-arith with
            btmock/strong and btmock/weak, which runs offline.
          </Empty>
        ) : (
          <div className="-m-4 overflow-x-auto">
            <table className="w-full min-w-[760px] text-[13px]">
              <thead className="border-b border-line">
                <tr>
                  <th className={th}></th>
                  <th className={th}>Run</th>
                  <th className={th}>Benchmark</th>
                  <th className={th}>Model</th>
                  <th className={th}>Status</th>
                  <th className={th}>Progress</th>
                  <th className={`${th} text-right`}>Accuracy</th>
                  <th className={`${th} text-right`}>Errors</th>
                  <th className={`${th} text-right`}>Cost</th>
                  <th className={th}>Created</th>
                </tr>
              </thead>
              <tbody>
                {runs.data?.map((r) => (
                  <tr key={r.id} className="border-b border-line last:border-0 hover:bg-sunken">
                    <td className={td}>
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.id} for comparison`}
                        checked={selected.includes(r.id)}
                        onChange={() => toggle(r.id)}
                      />
                    </td>
                    <td className={`${td} font-mono text-xs`}>
                      <Link className="text-accent hover:underline" to={`/runs/${r.id}`}>
                        {r.id}
                      </Link>
                    </td>
                    <td className={td}>{r.benchmark}</td>
                    <td className={`${td} font-mono text-xs`}>{r.model}</td>
                    <td className={td}>
                      <Badge value={r.status} />
                    </td>
                    <td className={td}>
                      <Progress done={r.samples_done} total={r.samples_total} />
                    </td>
                    <td className={`${td} tabular text-right`}>{pct(r.accuracy)}</td>
                    <td className={`${td} tabular text-right`}>{r.n_error}</td>
                    <td className={`${td} tabular text-right`}>{money(r.cost_usd)}</td>
                    <td className={`${td} text-muted`}>{ago(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
