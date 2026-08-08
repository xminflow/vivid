// 安东尼之家后台接口。服务端是仓库里的 server/（FastAPI）。
// 开发时 vite 把 /api/admin 代理过去，见 vite.config.ts。
//
// 请求本身（登录态、401 处理、错误约定）在 @/lib/api，这里只列有哪些接口。

import { call, get } from '@/lib/api'

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

export type PageParams = {
  page: number
  pageSize: number
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
