/**
 * 跟服务端说话的那一层。
 *
 * 官网只用得上三个不需要登录的接口：提交预约、提交服务申请、换一个 COS 直传地址。
 * 小程序那套 wx.login 换 token 的登录态在浏览器里不存在，所以这里没有 Authorization——
 * 服务端对这三个接口本来就允许匿名提交（预约记录的 user_id 留空）。
 */
import { API_BASE } from './config'

/** 服务端约定的响应外壳：ok 表示业务成败，与 HTTP 状态码分开看 */
export interface ApiEnvelope {
  ok: boolean
  message?: string
  errors?: string[]
}

export interface UploadUrlResult extends ApiEnvelope {
  url: string
  key: string
}

/** 提交类接口统一的失败语义。message 是服务端给的中文提示，可直接展示 */
export class ApiError extends Error {
  readonly status: number
  readonly errors: string[]

  constructor(message: string, status: number, errors: string[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errors = errors
  }
}

async function post<T extends ApiEnvelope>(path: string, body: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (cause) {
    // 断网、DNS、被拦截都收敛到这里。原因留在控制台，界面上只说人话
    console.error('[api] 请求发不出去', path, cause)
    throw new ApiError('网络异常，请检查后重试', 0)
  }

  // 服务端出错时也可能返回非 JSON（比如网关的 HTML 错误页），解析失败不能当成功
  let data: T
  try {
    data = (await res.json()) as T
  } catch (cause) {
    console.error('[api] 响应不是 JSON', path, res.status, cause)
    throw new ApiError('服务异常，请稍后重试', res.status)
  }

  if (!res.ok || !data.ok) {
    throw new ApiError(data.message ?? '提交失败，请稍后重试', res.status, data.errors ?? [])
  }
  return data
}

/** 展厅预约。字段与 server/app/models.py 的校验、schema.sql 的 CHECK 约束一一对应 */
export interface AppointmentPayload {
  name: string
  phone: string
  visitorType: string
  visitDate: string
  partySize: number
  purpose: string
  note?: string
}

export function submitAppointment(payload: AppointmentPayload) {
  return post<ApiEnvelope & { id: number }>('/api/appointments', payload)
}

/**
 * 服务申请。字段名要和服务端 app/models.py 的 ServiceApplicationIn 对上：
 * fields 是按服务定义动态收集的键值对（不是 form），images 是每个上传组的 COS 对象键。
 * 服务端用 alias_generator=to_camel，所以这里出驼峰。
 */
export interface ServiceApplicationPayload {
  serviceId: string
  name: string
  phone: string
  fields: Record<string, string>
  images: Record<string, string[]>
}

export function submitServiceApplication(payload: ServiceApplicationPayload) {
  return post<ApiEnvelope & { id: number }>('/api/service-applications', payload)
}

/** 换一个短时效的 COS 直传地址。客户端不持有密钥，签名由服务端出 */
export function requestUploadUrl(scene: string, ext: string) {
  return post<UploadUrlResult>('/api/upload-url', { scene, ext })
}
