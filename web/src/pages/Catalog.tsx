import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Card, ErrorNote, num, td, th } from '../ui'

export default function CatalogPage() {
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const families = new Set(catalog.data?.map((e) => e.family)).size
  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-xl font-semibold">Catalog</h1>
        <p className="text-sm text-muted">
          {catalog.data?.length ?? '…'} variants across {families || '…'} benchmark families. Each variant pins task,
          arguments and grader; runs on different variants are not compared directly.
        </p>
      </div>
      <ErrorNote error={catalog.error} />
      <Card>
        <div className="-m-4 overflow-x-auto">
          <table className="w-full min-w-[900px] text-[13px]">
            <thead className="border-b border-line">
              <tr>
                <th className={th}>Benchmark</th>
                <th className={th}>Variant</th>
                <th className={`${th} text-right`}>Tasks</th>
                <th className={th}>Grader</th>
                <th className={th}>Needs</th>
                <th className={th}>License</th>
              </tr>
            </thead>
            <tbody>
              {catalog.data?.map((e) => (
                <tr key={e.ref} className="border-b border-line last:border-0 align-top">
                  <td className={td}>
                    <div className="font-mono text-xs font-semibold">{e.ref}</div>
                    <div className="text-xs text-muted">{e.family}</div>
                  </td>
                  <td className={td}>
                    {e.variant}
                    {e.notes && <div className="text-xs text-warn">{e.notes}</div>}
                  </td>
                  <td className={`${td} tabular text-right`}>{num(e.task_count)}</td>
                  <td className={td}>{e.grader}</td>
                  <td className={`${td} font-mono text-xs`}>
                    {[...e.requires, ...(e.sandbox ? ['docker'] : []), ...(e.offline ? ['offline'] : [])].join(', ')}
                  </td>
                  <td className={`${td} text-xs`}>{e.license ?? '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
