// 后台请求的统一出入口。
//
// 登录态注入和 401 处理只在这里写一遍：散到各业务页去判断，早晚有一个页面
// 漏掉，表现是「明明已经掉线了，那个页面还在转圈」。

const BASE = '/api/admin'
const TOKEN_KEY = 'antony-admin-token'

export const readToken = (): string => localStorage.getItem(TOKEN_KEY) ?? ''
export const writeToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY)

// 401 时通知外层把人送回登录页。由 session-context 在挂载时注册，
// 这里留一个空实现，免得注册之前发的请求炸掉
let onUnauthorized: () => void = () => {}
export const setUnauthorizedHandler = (fn: () => void): void => {
  onUnauthorized = fn
}

/**
 * 服务端约定：成功是 {ok:true, ...}，失败是 {ok:false, message}，HTTP 状态码同时表达。
 * 这里不吞异常也不给默认值——查不出来就该在页面上报错，
 * 悄悄返回空列表会被当成「今天没人预约」。
 */
export async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = readToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers })
  } catch (err) {
    throw new Error(`连不上后台服务：${err instanceof Error ? err.message : String(err)}`)
  }

  const body: unknown = await res.json().catch(() => null)

  if (res.status === 401) {
    // 登录态没了就地清掉并送回登录页。留着一个已经失效的 token 只会让
    // 下一个页面再撞一次 401
    clearToken()
    onUnauthorized()
  }

  if (!res.ok || !isOk(body)) {
    throw new Error(errorMessage(body) ?? `请求失败（HTTP ${res.status}）`)
  }
  return body as T
}

export async function get<T>(
  path: string,
  params: Record<string, string | number>,
): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== null && value !== undefined) query.set(key, String(value))
  }
  return call<T>(`${path}?${query}`)
}

function isOk(body: unknown): boolean {
  return typeof body === 'object' && body !== null && (body as { ok?: unknown }).ok === true
}

function errorMessage(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  const message = (body as { message?: unknown }).message
  return typeof message === 'string' ? message : null
}
