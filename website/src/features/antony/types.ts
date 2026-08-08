/** 安东尼之家后台接口的返回类型，与 server/app/admin.py 一一对应。 */

export interface PagedResult<T> {
  ok: true
  total: number
  page: number
  pageSize: number
  items: T[]
}

export interface Appointment {
  id: number
  name: string
  phone: string
  visitorType: string
  /** YYYY-MM-DD */
  visitDate: string
  partySize: number
  purpose: string
  note: string
  /** 从哪张展厅卡片点进表单的，可能没有 */
  spaceId: string | null
  /**
   * 提交人。未登录也能提交，所以可能为空。
   * 服务端出的是字符串——雪花 ID 有 18 位，超过 Number.MAX_SAFE_INTEGER，当数字用会丢精度
   */
  userId: string | null
  status: AppointmentStatus
  createdAt: string
}

export type AppointmentStatus = 'new' | 'confirmed' | 'visited' | 'cancelled'

// 筛选条件写成 type 而不是 interface：type 才有隐式索引签名，
// 才能作为 Record<string, string> 传给通用的取数函数
export type AppointmentQuery = {
  keyword: string
  visitorType: string
  purpose: string
  status: string
  visitDateFrom: string
  visitDateTo: string
}

/** 服务申请里的一张图。url 为 null 表示服务端没配 COS，签不出浏览地址 */
export interface ApplicationImage {
  key: string
  url: string | null
}

export interface ServiceApplication {
  id: number
  serviceId: ServiceId
  name: string
  phone: string
  /** 各服务自己的表单字段，键是字段 id，见 antony-casa/mock/service.js */
  fields: Record<string, string>
  /** 键是上传组 id */
  images: Record<string, ApplicationImage[]>
  status: ServiceApplicationStatus
  createdAt: string
}

export type ServiceId = 'design' | 'hardfit' | 'buyer' | 'aftersale' | 'resale'

export type ServiceApplicationStatus = 'new' | 'contacted' | 'closed'

export type ServiceApplicationQuery = {
  keyword: string
  serviceId: string
  status: string
  createdFrom: string
  createdTo: string
}

// ---------------------------------------------------------------------------
// 首页配图

/** 首页上的三个位置，与 server/app/models.py 的 HOME_SLOTS 逐字一致 */
export type HomeSlot = 'hero' | 'showroom' | 'activity'

/** 一张首页图。url 为 null 表示服务端没配 COS，拼不出可访问地址 */
export interface HomeMediaItem {
  /** 雪花 ID，服务端出的是字符串（18 位，超过 Number.MAX_SAFE_INTEGER） */
  id: string
  /** COS 对象键，保存时回传给服务端的就是它 */
  key: string
  url: string | null
}

export interface HomeMediaResult {
  ok: true
  slots: Record<HomeSlot, HomeMediaItem[]>
  /** 每个位置最多放几张，由服务端给，前端不写死 */
  limits: Record<HomeSlot, number>
}

export interface HomeMediaUploadResult {
  ok: true
  key: string
  url: string
}
