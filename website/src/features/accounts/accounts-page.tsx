import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { StatusBadge } from '@/components/status-badge'
import { formatTime } from '@/lib/format'

import {
  createAccount,
  deleteAccount,
  listAccounts,
  resetAccountPassword,
  setAccountStatus,
} from './api'
import type { Account } from './api'

// 与 server/app/security.py 的 PASSWORD_MIN_LEN 一致
const PASSWORD_MIN = 8

// tone 取值见 @/components/status-badge 的 StatusTone：只有 pending / active /
// done / muted 四种，正常的用 done，停用的用 muted
const STATUS_META = {
  active: { label: '正常', tone: 'done' },
  disabled: { label: '已停用', tone: 'muted' },
} as const

export function AccountsPage() {
  const [items, setItems] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<Account | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const body = await listAccounts()
      setItems(body.items)
    } catch (err) {
      // 出错就清空并弹提示：留着上一次的列表会让人以为刚建的号没建上
      setItems([])
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const toggle = async (account: Account) => {
    const next = account.status === 'active' ? 'disabled' : 'active'
    if (next === 'disabled' && !confirm(`停用「${account.username}」？他会立刻被踢下线。`)) return
    try {
      await setAccountStatus(account.id, next)
      toast.success(next === 'disabled' ? '已停用' : '已启用')
      await reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const remove = async (account: Account) => {
    if (!confirm(`删除「${account.username}」？删了就找不回来了，只是停用请点「停用」。`)) return
    try {
      await deleteAccount(account.id)
      toast.success('已删除')
      await reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">账号管理</h1>
        <span className="text-sm text-muted-foreground">共 {items.length} 个</span>
        <Button className="ml-auto" onClick={() => setCreating(true)}>
          新建账号
        </Button>
      </header>

      <p className="mb-3 text-sm text-muted-foreground">
        后台没有注册入口，账号只能在这里创建。超级管理员的账号密码在服务器配置文件里，
        不在这个列表中。
      </p>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-40">用户名</TableHead>
              <TableHead className="w-32">姓名</TableHead>
              <TableHead className="w-24">状态</TableHead>
              <TableHead className="w-36">最近登录</TableHead>
              <TableHead className="w-20 text-right">登录次数</TableHead>
              <TableHead className="w-36">创建时间</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 3 }, (_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={7}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  还没有其他管理员，点右上角新建
                </TableCell>
              </TableRow>
            ) : (
              items.map((account) => (
                <TableRow key={account.id}>
                  <TableCell className="font-medium">{account.username}</TableCell>
                  <TableCell>
                    {account.displayName || <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={STATUS_META[account.status]} />
                  </TableCell>
                  <TableCell>
                    {account.lastLoginAt ? (
                      formatTime(account.lastLoginAt)
                    ) : (
                      <span className="text-muted-foreground">从未登录</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{account.loginCount}</TableCell>
                  <TableCell>{formatTime(account.createdAt)}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button variant="outline" size="sm" onClick={() => setResetting(account)}>
                        重置密码
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => void toggle(account)}>
                        {account.status === 'active' ? '停用' : '启用'}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => void remove(account)}
                      >
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <CreateDialog open={creating} onOpenChange={setCreating} onCreated={reload} />
      <ResetDialog account={resetting} onClose={() => setResetting(null)} />
    </div>
  )
}

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => Promise<void>
}) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      await createAccount({ username: username.trim(), displayName: displayName.trim(), password })
      // 密码只在这一刻能看到，之后库里只有哈希，谁都读不回来
      toast.success(`已创建「${username.trim()}」，把初始密码告诉本人`)
      onOpenChange(false)
      setUsername('')
      setDisplayName('')
      setPassword('')
      await onCreated()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建账号</DialogTitle>
          <DialogDescription>
            初始密码由你设置并线下告诉本人，对方登录后可以自己改。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="new-username">用户名</Label>
            <Input
              id="new-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="字母、数字和 _ . -"
            />
            <p className="text-xs text-muted-foreground">3 到 32 位，创建后不能改</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-display-name">姓名</Label>
            <Input
              id="new-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="可留空"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-account-password">初始密码</Label>
            <Input
              id="new-account-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">{PASSWORD_MIN} 到 64 位</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={submit}
            disabled={busy || username.trim().length < 3 || password.length < PASSWORD_MIN}
          >
            {busy ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResetDialog({
  account,
  onClose,
}: {
  account: Account | null
  onClose: () => void
}) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!account) return
    setBusy(true)
    try {
      await resetAccountPassword(account.id, password)
      toast.success(`已重置「${account.username}」的密码，他已被踢下线`)
      setPassword('')
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={account !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重置密码</DialogTitle>
          <DialogDescription>
            给「{account?.username}」设一个新密码。他当前的登录会立刻失效，需要用新密码重登。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-1.5">
          <Label htmlFor="reset-password">新密码</Label>
          <Input
            id="reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">{PASSWORD_MIN} 到 64 位</p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy || password.length < PASSWORD_MIN}>
            {busy ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
