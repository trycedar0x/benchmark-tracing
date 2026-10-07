import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, LogOut, Settings } from 'lucide-react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Toaster } from '@/components/ui/sonner'
import { api, ApiError, selectWorkspace, type Me } from './api'
import CatalogPage from './pages/Catalog'
import ComparePage from './pages/Compare'
import DatasetPage from './pages/Dataset'
import DatasetsPage from './pages/Datasets'
import ImportsPage from './pages/Imports'
import LoginPage from './pages/Login'
import NewRunPage from './pages/NewRun'
import RunPage from './pages/Run'
import RunsPage from './pages/Runs'
import SettingsPage from './pages/Settings'
import TracePage from './pages/Trace'
import TraceDiffPage from './pages/TraceDiff'
import TracesPage from './pages/Traces'

const links = [
  { to: '/', label: 'Runs', end: true },
  { to: '/new', label: 'New run' },
  { to: '/compare', label: 'Compare' },
  { to: '/traces', label: 'Traces' },
  { to: '/datasets', label: 'Datasets' },
  { to: '/imports', label: 'Imports' },
  { to: '/catalog', label: 'Catalog' },
]

function Account({ me }: { me: Me }) {
  const qc = useQueryClient()
  const logout = useMutation({ mutationFn: api.logout, onSuccess: () => qc.invalidateQueries() })
  return (
    <div className="ml-auto flex items-center gap-2">
      {me.workspaces.length > 1 && (
        <Select
          value={me.workspace?.id}
          onValueChange={(id) => {
            selectWorkspace(id)
            qc.invalidateQueries()
          }}
        >
          <SelectTrigger aria-label="Workspace" size="sm" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {me.workspaces.map((w) => (
              <SelectItem key={w.id} value={w.id}>
                {w.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {me.auth_enabled && (
        <span className="hidden text-sm text-muted-foreground md:inline">
          {me.workspaces.length <= 1 && me.workspace ? `${me.workspace.name} · ` : ''}
          {me.user}
        </span>
      )}
      <NavLink to="/settings">
        {({ isActive }) => (
          <Button variant={isActive ? 'secondary' : 'ghost'} size="icon-sm" aria-label="Settings" asChild>
            <span>
              <Settings />
            </span>
          </Button>
        )}
      </NavLink>
      {me.auth_enabled && (
        <Button variant="ghost" size="icon-sm" aria-label="Sign out" onClick={() => logout.mutate()}>
          <LogOut />
        </Button>
      )}
    </div>
  )
}

export default function App() {
  const me = useQuery({ queryKey: ['me'], queryFn: api.me, retry: false })

  if (me.error instanceof ApiError && me.error.status === 401) return <LoginPage />
  if (!me.data) return <Skeleton className="m-6 h-10 w-64" />

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
          <Account me={me.data} />
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<RunsPage />} />
          <Route path="/new" element={<NewRunPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/traces" element={<TracesPage />} />
          <Route path="/traces/:traceId" element={<TracePage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/diff" element={<TraceDiffPage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/datasets/:datasetId" element={<DatasetPage />} />
          <Route path="/imports" element={<ImportsPage />} />
          <Route path="/settings" element={<SettingsPage me={me.data} />} />
          <Route path="*" element={<p className="text-muted-foreground">Page not found.</p>} />
        </Routes>
      </main>
      <Toaster />
    </div>
  )
}
