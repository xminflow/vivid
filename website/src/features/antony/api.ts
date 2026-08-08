// 安东尼之家后台接口。服务端是仓库里的 server/（FastAPI）。
// 开发时 vite 把 /api/admin 代理过去，见 vite.config.ts。

import type {
  Appointment,
  AppointmentQuery,
  HomeMediaResult,
  HomeMediaUploadResult,
  HomeSlot,
  PagedResult,
  ServiceApplication,
  ServiceApplicationQuery,
} from './types'

const BASE = '/api/admin'

export type PageParams = {
  page: number
  pageSize: number
}

/**
 * 服务端约定：成功是 {ok:true, ...}，失败是 {ok:false, message}，HTTP 状态码同时表达。
 * 这里不吞异常也不给默认值——查不出来就该在页面上报错，
 * 悄悄返回空列表会被当成「今天没人预约」。
 */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch (err) {
    throw new Error(`连不上后台服务：${err instanceof Error ? err.message : String(err)}`)
  }

  const body: unknown = await res.json().catch(() => null)
  if (!res.ok || !isOk(body)) {
    throw new Error(errorMessage(body) ?? `请求失败（HTTP ${res.status}）`)
  }
  return body as T
}

async function get<T>(path: string, params: Record<string, string | number>): Promise<T> {
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

export const listAppointments = (params: AppointmentQuery & PageParams) =>
  get<PagedResult<Appointment>>('/appointments', params)

export const listServiceApplications = (params: ServiceApplicationQuery & PageParams) =>
  get<PagedResult<ServiceApplication>>('/service-applications', params)

// ---------------------------------------------------------------------------
// 首页配图

export const getHomeMedia = () => call<HomeMediaResult>('/home-media')

/** 整组替换某个位置的图，数组顺序即首页上的展示顺序。传空数组是清空。 */
export const saveHomeMedia = (slot: HomeSlot, keys: string[]) =>
  call<{ ok: true; slot: HomeSlot; count: number }>(`/home-media/${slot}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys }),
  })

/**
 * 传一张图，拿回 COS 对象键。
 *
 * 请求体是裸的文件字节，不是 FormData：服务端解析 multipart 要多装一个依赖，
 * 而这里一次只传一张图，裸 body 就够（服务端按文件头判类型，不看文件名）。
 * 只上传、不落库——运营点了「保存」才会写进配置。
 */
export const uploadHomeImage = (file: File) =>
  call<HomeMediaUploadResult>('/home-media/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })
