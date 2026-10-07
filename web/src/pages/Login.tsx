import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity } from 'lucide-react'
import { useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { errorMessage } from '@/lib/format'
import { api } from '../api'

export default function LoginPage() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: () => api.login(email, password),
    onSuccess: () => qc.invalidateQueries(),
  })

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-4" /> everyeval
          </CardTitle>
          <CardDescription>Sign in to your workspace.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              login.mutate()
            }}
          >
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="email">Email</FieldLabel>
                <Input
                  id="email"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="password">Password</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              {login.error && (
                <Alert variant="destructive">
                  <AlertDescription>{errorMessage(login.error)}</AlertDescription>
                </Alert>
              )}
              <Button type="submit" disabled={!email || !password || login.isPending}>
                Sign in
              </Button>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
