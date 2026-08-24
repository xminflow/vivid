/**
 * 安家立业后台接口的返回类型，与 server/app/anjia/ 下的 admin.py、cases_admin.py
 * 一一对应。
 */

import type { PagedResult } from '@/lib/paging'

/** 与服务端 app/anjia/certifications.py 的取值逐字一致 */
export type CertificationStatus = 'pending' | 'approved' | 'rejected' | 'revoked'

/**
 * 审核队列里的一行：一条企业认证申请，外加申请人的情况。
 *
 * 申请人那几项（昵称、注册时间、是否会员）是服务端 join 出来的，不是第二次请求。
 * 审核时要判断「这是不是一个刚注册来蹭认证的空号」，两屏之间来回查是查不出来的。
 */
export interface Certification {
  id: string
  userId: string
  companyName: string
  contactName: string
  contactPhone: string
  status: CertificationStatus
  rejectReason: string
  reviewedBy: string
  reviewedAt: string | null
  createdAt: string
  nickname: string
  isMember: boolean
  userCreatedAt: string
}

export interface CertificationQuery extends Record<string, string> {
  status: string
  keyword: string
}

/** 安家立业的注册用户。只读——这一期后台不封禁、也不代开会员 */
export interface AnjiaUser {
  id: string
  nickname: string
  isMember: boolean
  memberSince: string | null
  memberExpiresAt: string | null
  createdAt: string
  lastLoginAt: string | null
  loginCount: number
}

export interface AnjiaUserQuery extends Record<string, string> {
  keyword: string
  member: string
}

/** 与服务端 app/anjia/cases.py 的取值逐字一致 */
export type CaseStatus = 'pending' | 'published' | 'rejected' | 'delisted'

/**
 * 一张案例图。宽高由服务端在上传时读文件头得出、焊进对象键，前端不参与——
 * 小程序的双列瀑布流靠它在图片加载前占位（见 server/app/anjia/cases.py）。
 */
export interface CaseImage {
  url: string | null
  w: number
  h: number
}

/**
 * 审核队列里的一行：一条案例，外加作者的情况。
 *
 * 图片给的是**全部**而不是封面：审核就是要把内容看全再判，只给封面的话运营点
 * 「通过」时其实没看过后面那几张。
 */
export interface Case {
  id: string
  userId: string
  title: string
  body: string
  images: CaseImage[]
  status: CaseStatus
  statusLabel: string
  views: number
  rejectReason: string
  rejectNote: string
  reviewedBy: string
  reviewedAt: string | null
  publishedAt: string | null
  createdAt: string
  /** 与 reviewedAt 比对就知道「过审之后又被作者改过没有」 */
  updatedAt: string
  author: {
    id: string
    companyName: string
    nickname: string
    avatarUrl: string
    isMember: boolean
    createdAt: string
  }
}

export interface CaseQuery extends Record<string, string> {
  status: string
  keyword: string
}

/** 四个分页各有多少条，随列表一起下发——前端不为一个角标再发一次请求 */
export type CaseCounts = Record<CaseStatus, number>

export interface CaseListResult extends PagedResult<Case> {
  counts: CaseCounts
}
