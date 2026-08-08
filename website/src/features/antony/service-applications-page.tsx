import { useCallback, useState } from 'react'

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
import { StatusBadge } from '@/components/status-badge'

import { listServiceApplications } from './api'
import {
  SERVICES,
  SERVICE_STATUSES,
  fieldLabel,
  fieldValue,
  serviceLabel,
  statusMeta,
  uploadLabel,
} from './constants'
import type { ServiceApplication, ServiceApplicationQuery } from './types'
import { formatTime, usePagedList } from './use-paged-list'

const BLANK: ServiceApplicationQuery = {
  keyword: '',
  serviceId: '',
  status: '',
  createdFrom: '',
  createdTo: '',
}

const imageCount = (row: ServiceApplication) =>
  Object.values(row.images).reduce((sum, group) => sum + group.length, 0)

/** 表格里只放一行摘要，完整表单在抽屉里看 */
const summary = (row: ServiceApplication) =>
  Object.entries(row.fields)
    .map(([id, value]) => `${fieldLabel(id)} ${fieldValue(id, value)}`)
    .join(' · ')

export function ServiceApplicationsPage() {
  const list = usePagedList<ServiceApplication, ServiceApplicationQuery>(
    listServiceApplications,
    BLANK,
  )
  const [detail, setDetail] = useState<ServiceApplication | null>(null)

  const pick = useCallback(
    <K extends keyof ServiceApplicationQuery>(key: K, value: ServiceApplicationQuery[K]) => {
      list.setFilter(key, value)
      list.search()
    },
    [list],
  )

  // 没配 COS 时服务端签不出地址，url 为 null。这种情况要说出来，不能让运营以为客户没传图
  const missingImageUrl =
    detail !== null &&
    Object.values(detail.images)
      .flat()
      .some((img) => img.url === null)

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">服务申请</h1>
        <span className="text-sm text-muted-foreground">共 {list.total.toLocaleString()} 条</span>
      </header>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="w-56"
          placeholder="搜索客户名称或手机号，回车查询"
          value={list.filters.keyword}
          onChange={(e) => list.setFilter('keyword', e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') list.search()
          }}
        />
        <SelectFilter
          className="w-44"
          placeholder="服务类别"
          value={list.filters.serviceId}
          options={SERVICES.map((s) => ({ value: s.value, label: s.label }))}
          onChange={(value) => pick('serviceId', value)}
        />
        <SelectFilter
          className="w-32"
          placeholder="跟进状态"
          value={list.filters.status}
          options={SERVICE_STATUSES.map((s) => ({ value: s.value, label: s.label }))}
          onChange={(value) => pick('status', value)}
        />
        <div className="flex items-center gap-1 text-sm text-muted-foreground">
          <span>提交日期</span>
          <Input
            type="date"
            className="w-36"
            aria-label="提交日期从"
            value={list.filters.createdFrom}
            onChange={(e) => pick('createdFrom', e.target.value)}
          />
          <span>至</span>
          <Input
            type="date"
            className="w-36"
            aria-label="提交日期到"
            value={list.filters.createdTo}
            onChange={(e) => pick('createdTo', e.target.value)}
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
              <TableHead className="w-36">服务类别</TableHead>
              <TableHead className="w-28">客户名称</TableHead>
              <TableHead className="w-32">联系方式</TableHead>
              <TableHead className="w-16 text-right">图片</TableHead>
              <TableHead className="w-24">状态</TableHead>
              <TableHead>表单摘要</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading ? (
              <SkeletonRows />
            ) : list.items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  没有符合条件的申请
                </TableCell>
              </TableRow>
            ) : (
              list.items.map((row) => (
                <TableRow key={row.id} className="cursor-pointer" onClick={() => setDetail(row)}>
                  <TableCell>{formatTime(row.createdAt)}</TableCell>
                  <TableCell>{serviceLabel(row.serviceId)}</TableCell>
                  <TableCell>{row.name}</TableCell>
                  <TableCell className="tabular-nums">{row.phone}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {imageCount(row) || <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={statusMeta(SERVICE_STATUSES, row.status)} />
                  </TableCell>
                  <TableCell className="max-w-md truncate" title={summary(row)}>
                    {summary(row) || (
                      <span className="text-muted-foreground">（只填了联系方式）</span>
                    )}
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
        <SheetContent className="w-[520px] overflow-y-auto sm:max-w-[520px]">
          <SheetHeader>
            <SheetTitle>申请详情</SheetTitle>
            <SheetDescription>
              {detail ? `${serviceLabel(detail.serviceId)} · ${detail.name}` : ''}
            </SheetDescription>
          </SheetHeader>
          {detail && (
            <div className="px-4 pb-6">
              <DetailField label="服务类别">{serviceLabel(detail.serviceId)}</DetailField>
              <DetailField label="客户名称">{detail.name}</DetailField>
              <DetailField label="联系方式">{detail.phone}</DetailField>
              <DetailField label="状态">
                <StatusBadge status={statusMeta(SERVICE_STATUSES, detail.status)} />
              </DetailField>
              <DetailField label="提交时间">{formatTime(detail.createdAt)}</DetailField>
              {/* 各服务的表单字段不同，按提交时的键逐条列出；没登记中文名的显示原始 id */}
              {Object.entries(detail.fields).map(([id, value]) => (
                <DetailField key={id} label={fieldLabel(id)}>
                  {fieldValue(id, value)}
                </DetailField>
              ))}

              {Object.entries(detail.images).map(([groupId, group]) => (
                <section key={groupId} className="mt-5">
                  <h2 className="mb-2 text-sm font-medium">
                    {uploadLabel(groupId)}
                    <span className="ml-2 text-muted-foreground">{group.length} 张</span>
                  </h2>
                  <div className="flex flex-wrap gap-2">
                    {group.map((img) =>
                      img.url ? (
                        <a key={img.key} href={img.url} target="_blank" rel="noreferrer">
                          <img
                            src={img.url}
                            alt={img.key}
                            loading="lazy"
                            className="size-24 rounded-md border border-border object-cover"
                          />
                        </a>
                      ) : (
                        // 图片服务没配时不假装没有图：客户确实传了，只是这里签不出地址
                        <div
                          key={img.key}
                          title={img.key}
                          className="flex size-24 items-center justify-center rounded-md border border-dashed border-border p-2 text-center text-xs text-amber-700"
                        >
                          地址签发失败
                        </div>
                      ),
                    )}
                  </div>
                </section>
              ))}

              {missingImageUrl && (
                <p className="mt-4 rounded-md bg-amber-100 p-3 text-xs text-amber-900">
                  部分图片无法显示：服务端未配置 COS（COS_SECRET_ID / COS_BUCKET 等）
                </p>
              )}
              {Object.keys(detail.images).length === 0 && (
                <p className="mt-5 text-sm text-muted-foreground">客户没有上传图片</p>
              )}
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
          <TableCell colSpan={7}>
            <Skeleton className="h-5 w-full" />
          </TableCell>
        </TableRow>
      ))}
    </>
  )
}
