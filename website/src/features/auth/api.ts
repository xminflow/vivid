// 管理端登录相关接口。服务端见 server/app/admin_auth.py。

import { call } from '@/lib/api'

export type AdminUser = {
  username: string
  displayName: string
  /** 超管额外能进「账号管理」。服务端也拦，这个值只用来决定菜单显不显示 */
  isSuper: boolean
}

type LoginResult = { ok: true; token: string; expiresAt: string; user: AdminUser }

export const login = (username: string, password: string) =>
  call<LoginResult>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

export const logout = () => call<{ ok: true }>('/auth/logout', { method: 'POST' })

export const fetchMe = () => call<{ ok: true; user: AdminUser }>('/auth/me')

export const changeMyPassword = (oldPassword: string, newPassword: string) =>
  call<{ ok: true }>('/auth/password', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ oldPassword, newPassword }),
  })
