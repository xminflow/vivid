import type { StatusMeta } from '@/components/status-badge'

import type { CaseStatus, CertificationStatus } from './types'

/**
 * 认证状态的展示。颜色只表达「要不要处理」：待审核的显眼，其余收敛——
 * 一屏几十行，每个状态一个新颜色反而看不出哪条要动手。
 */
export const CERT_STATUS: Record<CertificationStatus, StatusMeta> = {
  pending: { label: '待审核', tone: 'pending' },
  approved: { label: '已通过', tone: 'done' },
  rejected: { label: '已驳回', tone: 'muted' },
  revoked: { label: '已撤销', tone: 'muted' },
}

export const CERT_STATUS_OPTIONS = [
  { value: 'pending', label: '待审核' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已驳回' },
  { value: 'revoked', label: '已撤销' },
] as const

export const MEMBER_OPTIONS = [
  { value: 'true', label: '会员' },
  { value: 'false', label: '非会员' },
] as const

/**
 * 案例状态的展示。同上：颜色只表达「要不要处理」。
 *
 * 已下架单独一个状态而不是并进「已通过」：运营下架时最关心的正是「这条现在前台
 * 还在不在」，混在一起就分不出来了。
 */
export const CASE_STATUS: Record<CaseStatus, StatusMeta> = {
  pending: { label: '待审核', tone: 'pending' },
  published: { label: '已发布', tone: 'done' },
  rejected: { label: '已驳回', tone: 'muted' },
  delisted: { label: '已下架', tone: 'muted' },
}

/** 队列的四个分页。顺序就是一条案例可能经过的顺序，默认停在第一个 */
export const CASE_TABS = [
  { value: 'pending', label: '待审核' },
  { value: 'published', label: '已发布' },
  { value: 'rejected', label: '已驳回' },
  { value: 'delisted', label: '已下架' },
] as const
