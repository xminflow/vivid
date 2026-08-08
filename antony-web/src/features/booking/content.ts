/**
 * 预约表单的选项。
 *
 * ⚠️ 这两组值现在有四处副本，必须逐字一致，改一处就要改四处：
 *   1. server/schema.sql 的 CHECK 约束
 *   2. server/app/models.py 的 VISITOR_TYPES / PURPOSES
 *   3. antony-casa/pages/booking/booking.js 的同名常量（小程序）
 *   4. 这里（官网）
 * 不一致的后果是表单能选、库里存不进去，而且要到用户点提交才暴露。
 */
export const VISITOR_TYPES = ['业主', '设计师', '地产圈', '家居圈', '酒店民宿圈', '艺术圈'] as const

export const PURPOSES = [
  '展厅参观',
  '全案设计咨询',
  '装修建材订购',
  '家具软装选购',
  '商务合作',
  '其他',
] as const

export const MIN_PARTY = 1
export const MAX_PARTY = 50

/** 服务端按本地日期比「不能早于今天」，这里也用本地日期，不做时区换算 */
export function todayStr(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
