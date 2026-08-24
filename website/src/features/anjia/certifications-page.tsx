import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { PaginationBar } from '@/components/pagination-bar'
import { SelectFilter } from '@/components/select-filter'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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

import {
  approveCertification,
  listCertifications,
  listUserCertifications,
  rejectCertification,
  revokeCertification,
} from './api'
import { CERT_STATUS, CERT_STATUS_OPTIONS } from './constants'
import type { Certification, CertificationQuery } from './types'

// 默认只看待审核的。已通过的会越积越多，默认全看等于没有默认——
// 这一页存在的意义是「有没有申请在等我」
const BLANK: CertificationQuery = { status: 'pending', keyword: '' }

/**
 * 企业认证审核。
 *
 * ## 认证从简，所以审核不能省
 *
 * 认证只核对公司全称、不验资质、不收营业执照（需求文档 4.3.2）。正因为闸门宽松，
 * 人工审核这一道就不能省——两者是配套的：企业背后有法人可追责，是首页内容流敢于
 * 只让企业发布的全部依据。
 *
 * ## 为什么每行都带着申请人的情况
 *
 * 光看一个公司名判断不了任何事。昵称、注册时间、是不是会员放在同一行，是为了
 * 让「这是个刚注册来蹭认证的空号」一眼可见。重名也一样：公司全称上没有唯一约束
 * （同名公司、分公司真实存在），判重只能靠人搜一下再看。
 */
export function CertificationsPage() {
  const list = usePagedList<Certification, CertificationQuery>(listCertifications, BLANK)
  const [rejecting, setRejecting] = useState<Certification | null>(null)
  const [history, setHistory] = useState<Certification | null>(null)
  const [busy, setBusy] = useState('')

  const act = async (row: Certification, run: () => Promise<unknown>, done: string) => {
    setBusy(row.id)
    try {
      await run()
      toast.success(done)
      list.reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">企业认证</h1>
        <span className="text-sm text-muted-foreground">共 {list.total.toLocaleString()} 条</span>
      </header>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="w-60"
          placeholder="搜公司名称"
          value={list.filters.keyword}
          onChange={(e) => list.setFilter('keyword', e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && list.search()}
        />
        <SelectFilter
          className="w-32"
          value={list.filters.status}
          options={CERT_STATUS_OPTIONS}
          placeholder="审核状态"
          onChange={(value) => {
            list.setFilter('status', value)
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
              <TableHead>公司名称</TableHead>
              <TableHead className="w-24">联系人</TableHead>
              <TableHead className="w-32">联系电话</TableHead>
              <TableHead className="w-44">申请人</TableHead>
              <TableHead className="w-20">状态</TableHead>
              <TableHead className="w-40">提交时间</TableHead>
              <TableHead className="w-48">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading &&
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={7}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))}

            {!list.loading && list.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                  {list.filters.status === 'pending'
                    ? '没有待审核的认证申请。'
                    : '没有符合条件的记录'}
                </TableCell>
              </TableRow>
            )}

            {!list.loading &&
              list.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">
                    {row.companyName}
                    {row.status === 'rejected' && row.rejectReason && (
                      <div className="text-xs font-normal text-muted-foreground">
                        驳回理由：{row.rejectReason}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>{row.contactName}</TableCell>
                  {/* 明文。驳回后打电话是唯一的补救手段，脱敏只会让人多点一次 */}
                  <TableCell className="font-mono text-xs">{row.contactPhone}</TableCell>
                  <TableCell>
                    <button
                      type="button"
                      className="text-left underline-offset-2 hover:underline"
                      onClick={() => setHistory(row)}
                      title="查看这个人的全部申请记录"
                    >
                      {row.nickname || '（未授权昵称）'}
                    </button>
                    <div className="text-xs text-muted-foreground">
                      {row.isMember ? '会员' : '非会员'} · 注册于{' '}
                      {formatTime(row.userCreatedAt).slice(0, 10)}
                    </div>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={CERT_STATUS[row.status]} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTime(row.createdAt)}
                  </TableCell>
                  <TableCell className="space-x-1">
                    {row.status === 'pending' && (
                      <>
                        <Button
                          size="sm"
                          disabled={busy === row.id}
                          onClick={() =>
                            void act(row, () => approveCertification(row.id), '已通过')
                          }
                        >
                          通过
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy === row.id}
                          onClick={() => setRejecting(row)}
                        >
                          驳回
                        </Button>
                      </>
                    )}
                    {row.status === 'approved' && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy === row.id}
                        onClick={() => {
                          // 撤销会让对方立刻失去首页发布权，问一句再动手
                          if (!confirm(`撤销「${row.companyName}」的企业认证？`)) return
                          void act(row, () => revokeCertification(row.id), '已撤销')
                        }}
                      >
                        撤销认证
                      </Button>
                    )}
                    {(row.status === 'rejected' || row.status === 'revoked') && (
                      <span className="text-xs text-muted-foreground">
                        {row.reviewedBy ? `${row.reviewedBy} 处理` : '—'}
                      </span>
                    )}
                  </TableCell>
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

      <RejectDialog
        row={rejecting}
        onClose={() => setRejecting(null)}
        onDone={list.reload}
      />
      <HistoryDialog row={history} onClose={() => setHistory(null)} />
    </div>
  )
}

/**
 * 驳回。理由必填——申请人在小程序里看到的就是这句话。
 *
 * 没有理由的驳回等于一个沉默的拒绝：对方既不知道该改什么，也只能靠反复提交去猜，
 * 而重新提交是不限次数的，最后堆回到这张队列上的还是同一批人。
 */
function RejectDialog({
  row,
  onClose,
  onDone,
}: {
  row: Certification | null
  onClose: () => void
  onDone: () => void
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  // 每次打开都从头填，免得上一条的理由留在框里被误提交
  useEffect(() => {
    if (row) setReason('')
  }, [row])

  const submit = async () => {
    if (!row) return
    if (!reason.trim()) {
      toast.error('请填写驳回理由')
      return
    }
    setBusy(true)
    try {
      await rejectCertification(row.id, reason.trim())
      toast.success('已驳回')
      onClose()
      onDone()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={row !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>驳回认证申请</DialogTitle>
          <DialogDescription>{row ? row.companyName : ''}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-1.5">
          <Label htmlFor="reject-reason">驳回理由</Label>
          <textarea
            id="reject-reason"
            rows={3}
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例如：公司名称不完整，请填写营业执照上的全称"
            className="w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
          />
          <p className="text-xs text-muted-foreground">
            这句话会原样显示给申请人。驳回后对方可以修改并重新提交。
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={busy}>
            {busy ? '提交中…' : '确认驳回'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 一个人的全部申请记录。改过几版公司名、被驳回过几次，都在这儿。 */
function HistoryDialog({ row, onClose }: { row: Certification | null; onClose: () => void }) {
  const [items, setItems] = useState<Certification[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!row) return
    setLoading(true)
    listUserCertifications(row.userId)
      .then((body) => setItems(body.items))
      .catch((err: unknown) => {
        toast.error(err instanceof Error ? err.message : String(err))
        setItems([])
      })
      .finally(() => setLoading(false))
  }, [row])

  return (
    <Dialog open={row !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>申请记录</DialogTitle>
          <DialogDescription>
            {row ? `${row.nickname || '（未授权昵称）'} · 共 ${items.length} 条` : ''}
          </DialogDescription>
        </DialogHeader>

        {loading && <Skeleton className="h-20 w-full" />}

        {!loading && (
          <ul className="space-y-2 text-sm">
            {items.map((item) => (
              <li key={item.id} className="rounded-md border p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{item.companyName}</span>
                  <StatusBadge status={CERT_STATUS[item.status]} />
                </div>
                <div className="text-xs text-muted-foreground">
                  {item.contactName} · {item.contactPhone} · {formatTime(item.createdAt)}
                </div>
                {item.rejectReason && (
                  <div className="text-xs text-muted-foreground">
                    驳回理由：{item.rejectReason}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
