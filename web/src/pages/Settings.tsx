import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ago, errorMessage } from '@/lib/format'
import { api, type Me } from '../api'

function ApiKeys({ me }: { me: Me }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [role, setRole] = useState('member')
  const [created, setCreated] = useState<string | null>(null)
  const keys = useQuery({ queryKey: ['keys'], queryFn: api.keys })
  const create = useMutation({
    mutationFn: () => api.createKey(name, role),
    onSuccess: (r) => {
      setCreated(r.key)
      setName('')
      qc.invalidateQueries({ queryKey: ['keys'] })
    },
  })
  const revoke = useMutation({
    mutationFn: api.revokeKey,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['keys'] }),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>API keys</CardTitle>
        <CardDescription>For the SDK, OTLP exporters and scripts. Keys act within this workspace.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {!me.auth_enabled ? (
          <p className="text-sm text-muted-foreground">
            Authentication is off (local mode). Start the server with EVERYEVAL_AUTH=1 to use keys.
          </p>
        ) : (
          <>
            {created && (
              <Alert>
                <KeyRound />
                <AlertTitle>Copy this key now; it will not be shown again</AlertTitle>
                <AlertDescription>
                  <code className="break-all font-mono text-xs select-all">{created}</code>
                </AlertDescription>
              </Alert>
            )}
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                create.mutate()
              }}
            >
              <Field className="w-56">
                <FieldLabel htmlFor="key-name">Name</FieldLabel>
                <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="ci-pipeline" />
              </Field>
              <Field className="w-36">
                <FieldLabel htmlFor="key-role">Role</FieldLabel>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger id="key-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">Member</SelectItem>
                    <SelectItem value="viewer">Viewer</SelectItem>
                    {me.role === 'owner' && <SelectItem value="owner">Owner</SelectItem>}
                  </SelectContent>
                </Select>
              </Field>
              <Button type="submit" disabled={!name.trim() || create.isPending}>
                Create key
              </Button>
            </form>
            {create.error && (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(create.error)}</AlertDescription>
              </Alert>
            )}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.data?.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell>{k.name}</TableCell>
                    <TableCell className="font-mono text-xs">{k.prefix}…</TableCell>
                    <TableCell>
                      <Badge variant="outline">{k.role}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {k.last_used_at ? ago(k.last_used_at) : 'never'}
                    </TableCell>
                    <TableCell className="text-right">
                      {k.revoked_at ? (
                        <Badge variant="destructive">revoked</Badge>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => revoke.mutate(k.id)}>
                          Revoke
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Secrets({ me }: { me: Me }) {
  const qc = useQueryClient()
  const [name, setName] = useState('OPENAI_API_KEY')
  const [value, setValue] = useState('')
  const secrets = useQuery({ queryKey: ['secrets'], queryFn: api.secrets })
  const save = useMutation({
    mutationFn: () => api.setSecret(name.trim(), value),
    onSuccess: () => {
      setValue('')
      toast.success(`Stored ${name}`)
      qc.invalidateQueries({ queryKey: ['secrets'] })
    },
  })
  const remove = useMutation({
    mutationFn: api.deleteSecret,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['secrets'] }),
  })
  const canEdit = me.role === 'owner'

  return (
    <Card>
      <CardHeader>
        <CardTitle>Provider keys and import credentials</CardTitle>
        <CardDescription>
          Encrypted at rest. Passed only to this workspace’s runs and imports as environment variables, e.g.
          OPENAI_API_KEY, ANTHROPIC_API_KEY, LANGFUSE_SECRET_KEY. Values are never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {canEdit && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              save.mutate()
            }}
          >
            <FieldGroup className="sm:flex-row sm:items-end">
              <Field>
                <FieldLabel htmlFor="secret-name">Name</FieldLabel>
                <Input id="secret-name" value={name} onChange={(e) => setName(e.target.value.toUpperCase())} />
              </Field>
              <Field>
                <FieldLabel htmlFor="secret-value">Value</FieldLabel>
                <Input
                  id="secret-value"
                  type="password"
                  autoComplete="off"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </Field>
              <Button type="submit" disabled={!name.trim() || !value || save.isPending}>
                Save
              </Button>
            </FieldGroup>
            <FieldDescription className="mt-2">Requires EVERYEVAL_SECRET_KEY on the server.</FieldDescription>
          </form>
        )}
        {(save.error || secrets.error) && (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(save.error || secrets.error)}</AlertDescription>
          </Alert>
        )}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {secrets.data?.map((s) => (
              <TableRow key={s.name}>
                <TableCell className="font-mono text-xs">{s.name}</TableCell>
                <TableCell className="text-muted-foreground">{ago(s.updated_at)}</TableCell>
                <TableCell className="text-right">
                  {canEdit && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Delete ${s.name}`}
                      onClick={() => remove.mutate(s.name)}
                    >
                      <Trash2 />
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

export default function SettingsPage({ me }: { me: Me }) {
  return (
    <div className="grid gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          {me.workspace ? `Workspace ${me.workspace.name}, signed in as ${me.user} (${me.role}).` : 'Local mode.'}
        </p>
      </div>
      <ApiKeys me={me} />
      <Secrets me={me} />
    </div>
  )
}
