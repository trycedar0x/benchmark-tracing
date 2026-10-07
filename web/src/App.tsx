import { NavLink, Route, Routes } from 'react-router-dom'
import CatalogPage from './pages/Catalog'
import ComparePage from './pages/Compare'
import NewRunPage from './pages/NewRun'
import RunPage from './pages/Run'
import RunsPage from './pages/Runs'
import TracePage from './pages/Trace'

const links = [
  { to: '/', label: 'Runs', end: true },
  { to: '/new', label: 'New run' },
  { to: '/compare', label: 'Compare' },
  { to: '/catalog', label: 'Catalog' },
]

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-line bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <NavLink to="/" className="flex items-center gap-2 font-semibold">
            <img src="/favicon.svg" alt="" className="h-5 w-5" />
            benchtrace
          </NavLink>
          <nav className="flex flex-wrap gap-1">
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.end}
                className={({ isActive }) =>
                  `rounded px-2.5 py-1 text-[13px] ${isActive ? 'bg-sunken font-semibold text-fg' : 'text-muted hover:text-fg'}`
                }
              >
                {l.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<RunsPage />} />
          <Route path="/new" element={<NewRunPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/traces/:traceId" element={<TracePage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="*" element={<div className="text-muted">Page not found.</div>} />
        </Routes>
      </main>
    </div>
  )
}
