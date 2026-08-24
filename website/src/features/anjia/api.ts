// 安家立业后台接口。服务端是 server/app/anjia/admin.py。
//
// 路径全在 /api/admin/anjia/ 下：一个后台管一个矩阵，前缀是各小程序的分界。
// 请求本身（登录态、401 处理、错误约定）在 @/lib/api，这里只列有哪些接口。

import { call, get } from '@/lib/api'
import type { PageParams, PagedResult } from '@/lib/paging'

import type {
  AnjiaUser,
  AnjiaUserQuery,
  Case,
  CaseListResult,
  CaseQuery,
  Certification,
  CertificationQuery,
} from './types'

export const listCertifications = (params: CertificationQuery & PageParams) =>
  get<PagedResult<Certification>>('/anjia/certifications', params)

/** 看某个人的全部申请记录：同一个人改过几版公司名，本身就是风控信号 */
export const listUserCertifications = (userId: string) =>
  get<PagedResult<Certification>>('/anjia/certifications', {
    userId,
    page: 1,
    pageSize: 50,
  })

export const approveCertification = (id: string) =>
  call<{ ok: true }>(`/anjia/certifications/${id}/approve`, { method: 'POST' })

// 驳回理由必填。申请人在小程序里看到的就是这句话
export const rejectCertification = (id: string, reason: string) =>
  call<{ ok: true }>(`/anjia/certifications/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })

export const revokeCertification = (id: string) =>
  call<{ ok: true }>(`/anjia/certifications/${id}/revoke`, { method: 'POST' })

export const listAnjiaUsers = (params: AnjiaUserQuery & PageParams) =>
  get<PagedResult<AnjiaUser>>('/anjia/users', params)

// ---------------------------------------------------------------- 案例
// 服务端是 server/app/anjia/cases_admin.py

export const listCases = (params: CaseQuery & PageParams) =>
  get<CaseListResult>('/anjia/cases', params)

/**
 * 只要「有多少条待审」这个数字，不要那一页数据，所以 pageSize 给 1。
 * 不复用 listCases：那个的入参是 CaseQuery（全字符串的筛选条件），
 * 这里要的是一组写死的字面量，硬凑成 CaseQuery 只是为了迁就类型。
 */
export const countPendingCases = () =>
  get<CaseListResult>('/anjia/cases', { status: 'pending', page: 1, pageSize: 1 })

/** 看这个账号都发过什么：一条案例是不是广告，往往看前面那五条才看得出来 */
export const listUserCases = (userId: string) =>
  get<CaseListResult>('/anjia/cases', { userId, page: 1, pageSize: 20 })

export const readCase = (id: string) => get<{ ok: true; case: Case }>(`/anjia/cases/${id}`, {})

export const approveCase = (id: string) =>
  call<{ ok: true }>(`/anjia/cases/${id}/approve`, { method: 'POST' })

// 驳回理由必填，作者在「我的发布」里看到的就是这句话；note 是额外的补充说明
export const rejectCase = (id: string, reason: string, note: string) =>
  call<{ ok: true }>(`/anjia/cases/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, note }),
  })
