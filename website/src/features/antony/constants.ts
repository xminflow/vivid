/**
 * 安东尼之家的选项字典。后台把库里存的 id / 枚举值翻成中文全靠这里。
 *
 * ⚠️ 同步要求：这些值必须与下面四处**逐字一致**，改一处就要改到位，
 *    否则后台会把正常数据显示成原始 id：
 *      1. server/schema.sql 的 CHECK 约束
 *      2. server/app/models.py 的 VISITOR_TYPES / PURPOSES / SERVICE_IDS / *_STATUSES
 *      3. antony-casa/mock/*.js 与 pages/booking/booking.js 的同名常量（小程序）
 *      4. antony-web/src/features/booking/content.ts 的同名常量（官网）
 *
 *    表单字段（FIELD_LABELS / UPLOAD_LABELS）只存在于小程序的 mock/service.js：
 *    一期表单写死在小程序里，服务端只当 jsonb 原样收发，不认识字段含义。
 *    所以这份字典是小程序表单定义的镜像，加字段时要一起加。
 *    没登记的字段不会被隐藏，页面直接显示原始 id，避免运营漏看客户填的内容。
 */

import type { AppointmentStatus, ServiceApplicationStatus, ServiceId } from './types'

export const VISITOR_TYPES = ['业主', '设计师', '地产圈', '家居圈', '酒店民宿圈', '艺术圈'] as const

export const PURPOSES = [
  '展厅参观',
  '全案设计咨询',
  '装修建材订购',
  '家具软装选购',
  '商务合作',
  '其他',
] as const

/** tone 决定徽标配色：待跟进的要显眼，已完成、已取消的要淡 */
export interface StatusOption<T extends string> {
  value: T
  label: string
  tone: 'pending' | 'active' | 'done' | 'muted'
}

export const APPOINTMENT_STATUSES: StatusOption<AppointmentStatus>[] = [
  { value: 'new', label: '待跟进', tone: 'pending' },
  { value: 'confirmed', label: '已确认', tone: 'active' },
  { value: 'visited', label: '已到店', tone: 'done' },
  { value: 'cancelled', label: '已取消', tone: 'muted' },
]

export const SERVICE_STATUSES: StatusOption<ServiceApplicationStatus>[] = [
  { value: 'new', label: '待联系', tone: 'pending' },
  { value: 'contacted', label: '已联系', tone: 'active' },
  { value: 'closed', label: '已关闭', tone: 'muted' },
]

export const SERVICES: { value: ServiceId; label: string }[] = [
  { value: 'design', label: '全案设计服务' },
  { value: 'hardfit', label: '硬装施工服务' },
  { value: 'buyer', label: '商品买手服务' },
  { value: 'aftersale', label: '商品售后服务' },
  { value: 'resale', label: '商品流转服务' },
]

/**
 * 申请表单的字段名。五个服务共用一张表，字段 id 也跨服务复用（itemName 在
 * 买手 / 售后 / 流转里都出现），所以是一张扁平字典，不按服务分组。
 * unit 与小程序表单里的单位一致，缺了单位数字没法读（120 是平米还是万）。
 */
export const FIELD_LABELS: Record<string, { label: string; unit?: string }> = {
  projectType: { label: '项目类别' },
  address: { label: '项目地址' },
  area: { label: '建筑面积', unit: '平米' },
  budgetHard: { label: '硬装预算', unit: '万' },
  budgetSoft: { label: '软装预算', unit: '万' },
  brands: { label: '意向建材品牌' },
  style: { label: '意向风格偏好' },
  itemName: { label: '商品名称' },
  itemBrand: { label: '商品品牌' },
  secondHand: { label: '是否接受二手/中古' },
  quantity: { label: '数量', unit: '件' },
  budget: { label: '购买预算', unit: '元' },
  deadline: { label: '可接受的最长寻购期' },
  buyYear: { label: '商品购买年份' },
  demand: { label: '售后需求' },
  city: { label: '商品所在城市' },
  condition: { label: '商品新旧程度' },
  expectPrice: { label: '期望到手价', unit: '元' },
  note: { label: '备注说明' },
}

/** 图片上传组 */
export const UPLOAD_LABELS: Record<string, string> = {
  style: '意向风格参考图片',
  floorplan: '项目平面图',
  item: '商品图片',
  damage: '商品磨损处细节图片',
  receipt: '商品购物凭证',
}

export const serviceLabel = (id: string): string =>
  SERVICES.find((s) => s.value === id)?.label ?? id

export function statusMeta<T extends string>(
  options: StatusOption<T>[],
  value: string,
): StatusOption<string> {
  return options.find((s) => s.value === value) ?? { value, label: value, tone: 'muted' }
}

/** 未登记的字段照原样显示 id，宁可难看也不能把客户填的内容藏起来 */
export const fieldLabel = (id: string): string => FIELD_LABELS[id]?.label ?? id

export const fieldValue = (id: string, value: string): string => {
  const unit = FIELD_LABELS[id]?.unit
  return unit ? `${value} ${unit}` : value
}

export const uploadLabel = (id: string): string => UPLOAD_LABELS[id] ?? id
