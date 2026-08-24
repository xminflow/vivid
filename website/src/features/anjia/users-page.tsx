import { PaginationBar } from '@/components/pagination-bar'
import { SelectFilter } from '@/components/select-filter'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatTime } from '@/lib/format'
import { usePagedList } from '@/lib/use-paged-list'

import { listAnjiaUsers } from './api'
import { MEMBER_OPTIONS } from './constants'
import type { AnjiaUser, AnjiaUserQuery } from './types'

const BLANK: AnjiaUserQuery = { keyword: '', member: '' }

/**
 * 安家立业的注册用户。**只读**。
 *
 * 没有封禁：安家立业的用户表连状态列都没有，而这一期没有任何内容可封，加了等于
 * 把整条内容治理链提前拽进来。也没有后台开通 / 取消会员：会员是用户自助行为，
 * 多一个后台写入口，就多一处「这个人的会员是哪来的」说不清的地方。
 *
 * 到期日这一列只是展示。一期任何地方都不校验它，见
 * docs/adr/0002-会员到期时间只写不读.md——看到「已过期」的日期不代表这个人不是会员。
 */
export function AnjiaUsersPage() {
  const list = usePagedList<AnjiaUser, AnjiaUserQuery>(listAnjiaUsers, BLANK)

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">用户</h1>
        <span className="text-sm text-muted-foreground">共 {list.total.toLocaleString()} 人</span>
      </header>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="w-60"
          placeholder="搜昵称"
          value={list.filters.keyword}
          onChange={(e) => list.setFilter('keyword', e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && list.search()}
        />
        <SelectFilter
          className="w-32"
          value={list.filters.member}
          options={MEMBER_OPTIONS}
          placeholder="会员状态"
          onChange={(value) => {
            list.setFilter('member', value)
            list.search()
          }}
        />
        <Button variant="outline" onClick={list.search}>
          查询
        </Button>
        <Button variant="ghost" onClick={list.reset}>
          重置
        </Button>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>昵称</TableHead>
              <TableHead className="w-24">会员</TableHead>
              <TableHead className="w-32">会员到期</TableHead>
              <TableHead className="w-40">注册时间</TableHead>
              <TableHead className="w-40">最后登录</TableHead>
              <TableHead className="w-20 text-right">登录次数</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading &&
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={6}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))}

            {!list.loading && list.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                  没有符合条件的用户
                </TableCell>
              </TableRow>
            )}

            {!list.loading &&
              list.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">
                    {row.nickname || '（未授权昵称）'}
                  </TableCell>
                  <TableCell>
                    {row.isMember ? '会员' : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {row.memberExpiresAt ? formatTime(row.memberExpiresAt).slice(0, 10) : '—'}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTime(row.createdAt)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {row.lastLoginAt ? formatTime(row.lastLoginAt) : '—'}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{row.loginCount}</TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </div>

      <PaginationBar
        total={list.total}
        page={list.page}
        pageSize={list.pageSize}
        onPageChange={list.setPage}
        onPageSizeChange={list.setPageSize}
      />
    </div>
  )
}
