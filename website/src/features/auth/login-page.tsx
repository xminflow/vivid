import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useSession } from './session-context'

export function LoginPage() {
  const { user, ready, signIn } = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!ready) return null
  if (user) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from ?? '/antony/appointments'} replace />
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await signIn(username.trim(), password)
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? '/antony/appointments', { replace: true })
    } catch (err) {
      // 就地显示，不弹 toast：错误紧挨着输入框才看得见，
      // 而登录失败之后人的视线本来就在输入框上
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-80 rounded-lg border border-border bg-card p-6"
      >
        <h1 className="mb-1 text-base font-semibold">小程序矩阵 · 管理后台</h1>
        <p className="mb-5 text-sm text-muted-foreground">账号由超级管理员分配</p>

        <div className="mb-3 grid gap-1.5">
          <Label htmlFor="username">用户名</Label>
          <Input
            id="username"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>

        <div className="mb-4 grid gap-1.5">
          <Label htmlFor="password">密码</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {error && <p className="mb-3 text-sm text-destructive">{error}</p>}

        <Button type="submit" className="w-full" disabled={busy || !username || !password}>
          {busy ? '登录中…' : '登录'}
        </Button>
      </form>
    </div>
  )
}
