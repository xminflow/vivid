import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { PaginationBar } from '@/components/pagination-bar'
import { StatusBadge } from '@/components/status-badge'
import { formatTime, formatYuan } from '@/lib/format'

import { listPaymentAnomalies, resolvePaymentAnomaly } from './api'
import {
  ANOMALY_KIND_HINT,
  ANOMALY_KIND_LABEL,
  ANOMALY_RESOLVED_OPTIONS,
  ANOMALY_SOURCE_LABEL,
} from './constants'
import type { PaymentAnomaly, PaymentAnomalyQuery } from './types'
import { usePagedList } from '@/lib/use-paged-list'

// 默认只看待处理的，不是「全部」——已处理的会越积越多，
// 默认全看等于没有默认，而这一页存在的意义就是「有没有事要人处理」
const BLANK: PaymentAnomalyQuery = { resolved: 'open' }

/** 金额可能为空（order_not_found 没有「应付多少」），空的时候显示破折号而不是 ¥ 0.00 */
const money = (cents: number | null) => (cents === null ? '—' : `¥ ${formatYuan(cents)}`)

/**
 * 支付异常台账。
 *
 * ## 这一页和别的列表页不是一个性质
 *
 * 其余列表页是「看数据」，这一页是**待办**：每一条都意味着一笔钱已经到账，
 * 但订单没有正常流转。三种情况（金额不符、订单不存在、缺支付单号）在服务端
 * 最终都要向微信返回 SUCCESS——重投一百次结果一样，让它一直重投只会堵住
 * 微信的回调队列。所以**没有任何自动重试会把它们救回来**，这个列表是唯一
 * 能看见它们的地方。
 *
 * 因此：默认只显示待处理、有未处理项时顶部出横幅、每一类都带一句「该去哪儿查」。
 *
 * ## 为什么「标记已处理」不改订单状态
 *
 * 它只记录「人已经看过并处理了」。真正要做的事（补一笔订单、去商户平台退款、
 * 改状态）各有各的入口，在这里顺手做等于开一个绕过所有状态机的后门。
 */
export function PaymentAnomaliesPage() {
  const list = usePagedList<PaymentAnomaly, PaymentAnomalyQuery>(listPaymentAnomalies, BLANK)
  const [resolving, setResolving] = useState<PaymentAnomaly | null>(null)

  const openCount = list.filters.resolved === 'open' ? list.total : null

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">支付异常</h1>
        <span className="text-sm text-muted-foreground">共 {list.total.toLocaleString()} 条</span>
      </header>

      {/* 有未处理的就顶一条横幅。这一页可能几个月都是空的，真出事那天
          必须让人一眼看出「这不是一个普通列表」 */}
      {openCount !== null && openCount > 0 && (
        <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-200">
          有 {openCount} 笔支付对不上订单。<strong>钱已经到账</strong>，但订单没有正常流转，
          而且不会有任何自动重试把它们救回来——需要人拿微信支付单号去商户平台核对后处理。
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {/* 这里不用 SelectFilter：那个组件的「全部」对外是空串，而空串会被
            请求层丢掉，服务端就退回默认的 open——界面显示「全部」、实际只查待处理，
            是最坏的一种不一致。三个取值都显式发出去 */}
        <Select
          value={list.filters.resolved}
          onValueChange={(value) => {
            list.setFilter('resolved', value)
            list.search()
          }}
        >
          <SelectTrigger className="w-36" aria-label="处理状态">
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper" align="start">
            {ANOMALY_RESOLVED_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="ghost" onClick={list.reset}>
          重置
        </Button>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">类型</TableHead>
              <TableHead className="w-20">状态</TableHead>
              <TableHead className="w-44">商户订单号</TableHead>
              <TableHead className="w-44">微信支付单号</TableHead>
              <TableHead className="w-28 text-right">实付</TableHead>
              <TableHead className="w-28 text-right">应付</TableHead>
              <TableHead className="w-24">来源</TableHead>
              <TableHead className="w-40">发生时间</TableHead>
              <TableHead className="w-28">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading &&
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={9}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))}

            {!list.loading && list.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="py-10 text-center text-muted-foreground">
                  {list.filters.resolved === 'open'
                    ? '没有待处理的支付异常。这一页空着是好事。'
                    : '没有符合条件的记录'}
                </TableCell>
              </TableRow>
            )}

            {!list.loading &&
              list.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>{ANOMALY_KIND_LABEL[row.kind] ?? row.kind}</TableCell>
                  <TableCell>
                    <StatusBadge
                      status={
                        row.resolvedAt
                          ? { label: '已处理', tone: 'muted' }
                          : { label: '待处理', tone: 'pending' }
                      }
                    />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{row.orderNo}</TableCell>
                  {/* 支付单号是去商户平台核对的凭据，缺的那一类正是异常本身 */}
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {row.transactionId || '—'}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.paidCents)}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {money(row.expectedCents)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {ANOMALY_SOURCE_LABEL[row.source] ?? row.source}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTime(row.createdAt)}
                  </TableCell>
                  <TableCell>
                    {row.resolvedAt ? (
                      <span
                        className="text-xs text-muted-foreground"
                        title={`${row.resolvedBy} · ${row.resolveNote}`}
                      >
                        {row.resolvedBy || '—'}
                      </span>
                    ) : (
                      <Button size="sm" variant="outline" onClick={() => setResolving(row)}>
                        标记已处理
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </div>

      {/* 类型说明常驻在列表下方，不做成 tooltip：运营看到这一行时手里只有一个
          订单号和一笔到账的钱，需要的是「下一步去哪儿查」，而那句话得能被读到 */}
      {!list.loading && list.items.length > 0 && (
        <section className="mt-4 space-y-2 rounded-md border p-3 text-xs text-muted-foreground">
          <h2 className="text-sm font-medium text-foreground">这几类异常是什么意思</h2>
          {[...new Set(list.items.map((row) => row.kind))].map((kind) => (
            <p key={kind}>
              <span className="font-medium text-foreground">{ANOMALY_KIND_LABEL[kind]}</span>
              ：{ANOMALY_KIND_HINT[kind]}
            </p>
          ))}
        </section>
      )}

      <PaginationBar
        total={list.total}
        page={list.page}
        pageSize={list.pageSize}
        onPageChange={list.setPage}
        onPageSizeChange={list.setPageSize}
      />

      <ResolveDialog
        anomaly={resolving}
        onClose={() => setResolving(null)}
        onDone={list.reload}
      />
    </div>
  )
}

/**
 * 标记已处理。处理说明必填。
 *
 * 几个月后回看这张台账时，「谁在什么时候标了已处理」远不如「当时是怎么处理的」
 * 有用——那时人早就不记得这笔钱最后去哪了。
 */
function ResolveDialog({
  anomaly,
  onClose,
  onDone,
}: {
  anomaly: PaymentAnomaly | null
  onClose: () => void
  onDone: () => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // 每次打开都从头填，免得上一条的说明留在框里被误提交
  useEffect(() => {
    if (anomaly) setNote('')
  }, [anomaly])

  const submit = async () => {
    if (!anomaly) return
    if (!note.trim()) {
      toast.error('请填写处理说明')
      return
    }
    setBusy(true)
    try {
      await resolvePaymentAnomaly(anomaly.id, note.trim())
      toast.success('已标记为已处理')
      onClose()
      onDone()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={anomaly !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>标记为已处理</DialogTitle>
          <DialogDescription>
            {anomaly
              ? `${ANOMALY_KIND_LABEL[anomaly.kind]} · 订单 ${anomaly.orderNo}`
              : ''}
          </DialogDescription>
        </DialogHeader>

        {anomaly && (
          <div className="space-y-3">
            <div className="rounded-md border p-3 text-xs text-muted-foreground">
              {ANOMALY_KIND_HINT[anomaly.kind]}
            </div>

            <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted-foreground">微信支付单号</dt>
              <dd className="font-mono text-xs">{anomaly.transactionId || '—'}</dd>
              <dt className="text-muted-foreground">实付</dt>
              <dd className="tabular-nums">{money(anomaly.paidCents)}</dd>
              <dt className="text-muted-foreground">应付</dt>
              <dd className="tabular-nums">{money(anomaly.expectedCents)}</dd>
              <dt className="text-muted-foreground">发现于</dt>
              <dd>
                {ANOMALY_SOURCE_LABEL[anomaly.source]} · {formatTime(anomaly.createdAt)}
              </dd>
            </dl>

            <p className="text-xs text-muted-foreground">
              标记不会改动订单状态，也不会退钱——它只记录「这件事有人处理过了」。
            </p>

            <div className="grid gap-1.5">
              <Label htmlFor="resolve-note">处理说明</Label>
              <textarea
                id="resolve-note"
                rows={3}
                maxLength={500}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="例如：已在商户平台确认实收 0.01 元，原路退回，已电话通知客户重新下单"
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={busy}>
            {busy ? '保存中…' : '确认'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
