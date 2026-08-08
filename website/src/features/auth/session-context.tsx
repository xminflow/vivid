// 当前登录态。
//
// 启动时若本地有 token，先调一次 /auth/me 验过再放行，不直接信任本地缓存的
// 用户信息——账号可能已经被超管停用或删掉了，拿着旧缓存进去只会每个接口撞一次
// 401，看着像后台坏了。

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { clearToken, readToken, setUnauthorizedHandler, writeToken } from '@/lib/api'

import { fetchMe, login as loginRequest, logout as logoutRequest } from './api'
import type { AdminUser } from './api'

type Session = {
  user: AdminUser | null
  /** 首次 /auth/me 校验是否已经完成。没完成时不要下判断，否则会闪一下登录页 */
  ready: boolean
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

const SessionContext = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null)
  const [ready, setReady] = useState(false)

  // 任何一个请求撞了 401，都在这里把人打回未登录
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
  }, [])

  useEffect(() => {
    if (!readToken()) {
      setReady(true)
      return
    }
    let cancelled = false
    void fetchMe()
      .then((body) => {
        if (!cancelled) setUser(body.user)
      })
      // token 失效时 call() 已经清过 token 并通知过了，这里只需要别把异常抛出去
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (username: string, password: string) => {
    const body = await loginRequest(username, password)
    // 先写 token 再置 user：跳转后立刻发的请求要能带上它
    writeToken(body.token)
    setUser(body.user)
  }, [])

  const signOut = useCallback(async () => {
    try {
      await logoutRequest()
    } finally {
      // 服务端那条会话删没删成，本地都得退干净——退不掉的「退出登录」更糟
      clearToken()
      setUser(null)
    }
  }, [])

  return (
    <SessionContext value={{ user, ready, signIn, signOut }}>{children}</SessionContext>
  )
}

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession 必须在 SessionProvider 里用')
  return session
}
