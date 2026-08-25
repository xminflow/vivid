import { useCallback, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DetailField } from '@/components/detail-field'
import { PaginationBar } from '@/components/pagination-bar'
import { SelectFilter } from '@/components/select-filter'
import { formatTime } from '@/lib/format'

import { deleteAppointment, listAppointments } from './api'
import { PURPOSES, VISITOR_TYPES } from './constants'
import type { Appointment, AppointmentQuery } from './types'
import { usePagedList } from '@/lib/use-paged-list'

const BLANK: AppointmentQuery = {
  keyword: '',
  visitorType: '',
  purpose: '',
  visitDateFrom: '',
  visitDateTo: '',
}

const asOptions = (values: readonly string[]) => values.map((v) => ({ value: v, label: v }))

export function AppointmentsPage() {
  const list = usePagedList<Appointment, AppointmentQuery>(listAppointments, BLANK)
  const [detail, setDetail] = useState<Appointment | null>(null)

  // 下拉和日期选完就查；关键字要等回车，边打字边查会把没输完的词也发出去
  const pick = useCallback(
    <K extends keyof AppointmentQuery>(key: K, value: AppointmentQuery[K]) => {
      list.setFilter(key, value)
      list.search()
    },
    [list],
  )

  // 删除不可撤销，先 confirm 挡一道，确认文案里带上是谁的哪一条，
  // 避免点错行还照着确认框点「确定」
  const remove = async (row: Appointment) => {
    if (!confirm(`删除 ${row.name}（${row.phone}）${row.visitDate} 的预约？删了就找不回来了。`)) {
      return
    }
    try {
      await deleteAppointment(row.id)
      toast.success('已删除')
      // 抽屉里那条可能正是被删的，一并关掉
      setDetail(null)
      list.reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">展厅预约申请</h1>
        <span className="text-sm text-muted-foreground">共 {list.total.toLocaleString()} 条</span>
      </header>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="w-56"
          placeholder="搜索姓名或手机号，回车查询"
          value={list.filters.keyword}
          onChange={(e) => list.setFilter('keyword', e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') list.search()
          }}
        />
        <SelectFilter
          className="w-36"
          placeholder="来者身份"
          value={list.filters.visitorType}
          options={asOptions(VISITOR_TYPES)}
          onChange={(value) => pick('visitorType', value)}
        />
        <SelectFilter
          className="w-40"
          placeholder="预约需求"
          value={list.filters.purpose}
          options={asOptions(PURPOSES)}
          onChange={(value) => pick('purpose', value)}
        />
        <div className="flex items-center gap-1 text-sm text-muted-foreground">
          <span>到访日期</span>
          <Input
            type="date"
            className="w-36"
            aria-label="到访日期从"
            value={list.filters.visitDateFrom}
            onChange={(e) => pick('visitDateFrom', e.target.value)}
          />
          <span>至</span>
          <Input
            type="date"
            className="w-36"
            aria-label="到访日期到"
            value={list.filters.visitDateTo}
            onChange={(e) => pick('visitDateTo', e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={list.reset}>
          重置
        </Button>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-36">提交时间</TableHead>
              <TableHead className="w-24">称呼</TableHead>
              <TableHead className="w-32">手机号</TableHead>
              <TableHead className="w-28">来者身份</TableHead>
              <TableHead className="w-28">到访日期</TableHead>
              <TableHead className="w-20">到访时间</TableHead>
              <TableHead className="w-16 text-right">人数</TableHead>
              <TableHead className="w-32">预约需求</TableHead>
              <TableHead className="w-20">来源</TableHead>
              <TableHead>备注</TableHead>
              <TableHead className="w-20">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading ? (
              <SkeletonRows />
            ) : list.items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={11} className="h-24 text-center text-muted-foreground">
                  没有符合条件的预约
                </TableCell>
              </TableRow>
            ) : (
              list.items.map((row) => (
                <TableRow
                  key={row.id}
                  className="cursor-pointer"
                  onClick={() => setDetail(row)}
                >
                  <TableCell>{formatTime(row.createdAt)}</TableCell>
                  <TableCell>{row.name}</TableCell>
                  <TableCell className="tabular-nums">{row.phone}</TableCell>
                  <TableCell>{row.visitorType}</TableCell>
                  <TableCell className="tabular-nums">{row.visitDate}</TableCell>
                  <TableCell className="tabular-nums">
                    {/* 加这个字段之前的记录只有日期，几点到得回访时再问 */}
                    {row.visitTime ?? <span className="text-muted-foreground">未选</span>}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{row.partySize}</TableCell>
                  <TableCell>{row.purpose}</TableCell>
                  <TableCell>
                    {/* 未登录也能提交，能对上用户的才算会员提交 */}
                    {row.userId ? '会员' : <span className="text-muted-foreground">游客</span>}
                  </TableCell>
                  <TableCell className="max-w-64 truncate" title={row.note}>
                    {row.note || <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="destructive"
                      size="sm"
                      // 整行点击会打开详情抽屉，删除按钮要把事件挡在这里
                      onClick={(e) => {
                        e.stopPropagation()
                        void remove(row)
                      }}
                    >
                      删除
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
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

      <Sheet open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <SheetContent className="w-[420px] overflow-y-auto sm:max-w-[420px]">
          <SheetHeader>
            <SheetTitle>预约详情</SheetTitle>
            <SheetDescription>
              {detail ? `${detail.name} · ${detail.phone}` : ''}
            </SheetDescription>
          </SheetHeader>
          {detail && (
            <div className="px-4 pb-6">
              <DetailField label="称呼">{detail.name}</DetailField>
              <DetailField label="手机号">{detail.phone}</DetailField>
              <DetailField label="来者身份">{detail.visitorType}</DetailField>
              <DetailField label="到访日期">{detail.visitDate}</DetailField>
              <DetailField label="到访时间">
                {detail.visitTime ?? <span className="text-muted-foreground">（未选）</span>}
              </DetailField>
              <DetailField label="到访人数">{detail.partySize} 人</DetailField>
              <DetailField label="预约需求">{detail.purpose}</DetailField>
              <DetailField label="提交人">
                {detail.userId ? `会员 ${detail.userId}` : '未登录游客'}
              </DetailField>
              <DetailField label="提交时间">{formatTime(detail.createdAt)}</DetailField>
              <DetailField label="备注">
                {detail.note || <span className="text-muted-foreground">（无）</span>}
              </DetailField>

              <Button
                variant="destructive"
                className="mt-6 w-full"
                onClick={() => void remove(detail)}
              >
                删除这条预约
              </Button>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 5 }, (_, i) => (
        <TableRow key={i}>
          <TableCell colSpan={10}>
            <Skeleton className="h-5 w-full" />
          </TableCell>
        </TableRow>
      ))}
    </>
  )
}
