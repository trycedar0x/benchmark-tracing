import { Activity } from 'lucide-react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { Button } from '@/components/ui/button'
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
    <div className="min-h-svh bg-background">
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2">
          <NavLink to="/" className="flex items-center gap-2 font-semibold">
            <Activity className="size-4" />
            benchtrace
          </NavLink>
          <nav className="flex flex-wrap gap-1">
            {links.map((l) => (
              <NavLink key={l.to} to={l.to} end={l.end}>
                {({ isActive }) => (
                  <Button variant={isActive ? 'secondary' : 'ghost'} size="sm" asChild>
                    <span>{l.label}</span>
                  </Button>
                )}
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
          <Route path="*" element={<p className="text-muted-foreground">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  )
}
