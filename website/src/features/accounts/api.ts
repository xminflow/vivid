// 管理员账号管理。只有超管调得通，服务端见 server/app/admin_accounts.py。

import { call } from '@/lib/api'

export type Account = {
  /** 雪花 ID，字符串。别转成 number，18 位会丢精度 */
  id: string
  username: string
  displayName: string
  status: 'active' | 'disabled'
  lastLoginAt: string | null
  loginCount: number
  createdAt: string
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const listAccounts = () => call<{ ok: true; items: Account[] }>('/accounts')

export const createAccount = (input: {
  username: string
  displayName: string
  password: string
}) =>
  call<{ ok: true; account: Account }>('/accounts', { method: 'POST', ...json(input) })

export const resetAccountPassword = (id: string, password: string) =>
  call<{ ok: true }>(`/accounts/${id}/password`, { method: 'PUT', ...json({ password }) })

export const setAccountStatus = (id: string, status: Account['status']) =>
  call<{ ok: true; account: Account }>(`/accounts/${id}/status`, {
    method: 'PUT',
    ...json({ status }),
  })

export const deleteAccount = (id: string) =>
  call<{ ok: true }>(`/accounts/${id}`, { method: 'DELETE' })
