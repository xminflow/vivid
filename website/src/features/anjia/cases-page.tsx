import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { PaginationBar } from '@/components/pagination-bar'
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
import { cn } from '@/lib/utils'

import { approveCase, listCases, listUserCases, rejectCase } from './api'
import { CASE_STATUS, CASE_TABS } from './constants'
import type { Case, CaseListResult, CaseQuery, CaseStatus } from './types'

// 默认只看待审核的。已发布的会越积越多，默认全看等于没有默认——
// 这一页存在的意义是「有没有内容在等我审」
const BLANK: CaseQuery = { status: 'pending', keyword: '' }

/**
 * 案例审核。
 *
 * ## 为什么审核必须点进详情
 *
 * 一条案例最多九张图加一千字，列表里塞不下。就地点「通过」几乎等于没看——而
 * 人工先审后发是需求文档 2.3.1 对首页 UGC 的硬性要求，也是「企业认证从简、
 * 核验宽松」的配套：闸门放宽了，这一道就不能省。所以列表只给封面和判断依据，
 * 通过/驳回两个动作都在详情弹层里。
 *
 * ## 为什么有第四个分页
 *
 * 需求文档 2.3.3 只写了三个（待审核/已通过/已驳回），但还要求「支持已发布内容的
 * 事后下架」。下架的案例混进「已发布」，运营就分不清「这条现在前台还在不在」，
 * 而那正是他下架时最关心的一件事。所以单开一个。
 */
export function CasesPage() {
  const list = usePagedList<Case, CaseQuery, CaseListResult>(listCases, BLANK)
  const [reviewing, setReviewing] = useState<Case | null>(null)
  const [author, setAuthor] = useState<Case | null>(null)
  const counts = list.response?.counts

  const switchTab = (value: string) => {
    list.setFilter('status', value)
    list.search()
  }

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">案例</h1>
        <span className="text-sm text-muted-foreground">
          首页内容流 · 共 {list.total.toLocaleString()} 条
        </span>
      </header>

      {/* 四个分页。数量随列表一起下发，不为一个角标再发一次请求 */}
      <div className="mb-3 flex flex-wrap items-center gap-1 border-b">
        {CASE_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => switchTab(tab.value)}
            className={cn(
              '-mb-px border-b-2 px-3 py-2 text-sm transition-colors',
              list.filters.status === tab.value
                ? 'border-foreground font-medium text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
            {counts && counts[tab.value] > 0 && (
              <span
                className={cn(
                  'ml-1.5 text-xs',
                  // 只有待审核的数字要显眼：它是待办，其余三个是存量
                  tab.value === 'pending' ? 'text-amber-600 dark:text-amber-400' : 'opacity-60',
                )}
              >
                {counts[tab.value]}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="w-60"
          placeholder="搜标题或公司名"
          value={list.filters.keyword}
          onChange={(e) => list.setFilter('keyword', e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && list.search()}
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
              <TableHead className="w-20">封面</TableHead>
              <TableHead>标题</TableHead>
              <TableHead className="w-48">作者</TableHead>
              <TableHead className="w-16">图</TableHead>
              <TableHead className="w-16">浏览</TableHead>
              <TableHead className="w-20">状态</TableHead>
              <TableHead className="w-40">提交时间</TableHead>
              <TableHead className="w-24">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading &&
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={8}>
                    <Skeleton className="h-14 w-full" />
                  </TableCell>
                </TableRow>
              ))}

            {!list.loading && list.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="py-10 text-center text-muted-foreground">
                  {list.filters.status === 'pending'
                    ? '没有待审核的案例。'
                    : '没有符合条件的记录'}
                </TableCell>
              </TableRow>
            )}

            {!list.loading &&
              list.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    <Thumb image={row.images[0]} />
                  </TableCell>
                  <TableCell className="font-medium">
                    {row.title}
                    {row.status === 'rejected' && row.rejectReason && (
                      <div className="text-xs font-normal text-muted-foreground">
                        驳回理由：{row.rejectReason}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <button
                      type="button"
                      className="text-left underline-offset-2 hover:underline"
                      onClick={() => setAuthor(row)}
                      title="看这个账号都发过什么"
                    >
                      {row.author.companyName || row.author.nickname || '（未命名账号）'}
                    </button>
                    <div className="text-xs text-muted-foreground">
                      {row.author.isMember ? '会员' : '非会员'} · 注册于{' '}
                      {formatTime(row.author.createdAt).slice(0, 10)}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{row.images.length}</TableCell>
                  <TableCell className="text-muted-foreground">{row.views}</TableCell>
                  <TableCell>
                    <StatusBadge status={CASE_STATUS[row.status]} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTime(row.createdAt)}
                  </TableCell>
                  <TableCell>
                    <Button size="sm" variant="outline" onClick={() => setReviewing(row)}>
                      {row.status === 'pending' ? '审核' : '查看'}
                    </Button>
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

      <ReviewDialog
        row={reviewing}
        onClose={() => setReviewing(null)}
        onDone={() => {
          setReviewing(null)
          list.reload()
        }}
      />
      <AuthorDialog row={author} onClose={() => setAuthor(null)} />
    </div>
  )
}

/**
 * 一张图。COS 没配时 url 是 null——画一个占位而不是一张裂图，
 * 后者会让人以为是这条案例的图坏了，而不是环境没配好。
 */
function Thumb({ image }: { image?: { url: string | null } }) {
  if (!image?.url) {
    return (
      <div className="flex size-14 items-center justify-center rounded bg-muted text-xs text-muted-foreground">
        无图
      </div>
    )
  }
  return <img src={image.url} alt="" className="size-14 rounded object-cover" loading="lazy" />
}

/**
 * 审核详情。图文看全，再决定通过还是驳回。
 *
 * 驳回理由是**填写**而不是从预设里选：第一刀先这样，第二刀换成一组预设选项。
 * 接口的形状（reason + note）现在就定死了，到时只改这一个弹层，不动接口。
 */
function ReviewDialog({
  row,
  onClose,
  onDone,
}: {
  row: Case | null
  onClose: () => void
  onDone: () => void
}) {
  const [mode, setMode] = useState<'view' | 'reject'>('view')
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // 每次打开都从头来，免得上一条的驳回理由留在框里被误提交
  useEffect(() => {
    if (row) {
      setMode('view')
      setReason('')
      setNote('')
    }
  }, [row])

  const act = async (run: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await run()
      toast.success(done)
      onDone()
    } catch (err) {
      // 409 的文案是「可能已被其他人处理，请刷新后再看」——那是乐观锁在说话，
      // 原样弹出来就够，不用在这里再翻译一遍
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const submitReject = () => {
    if (!row) return
    if (!reason.trim()) {
      toast.error('请填写驳回理由')
      return
    }
    void act(() => rejectCase(row.id, reason.trim(), note.trim()), '已驳回')
  }

  return (
    <Dialog open={row !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{row?.title}</DialogTitle>
          <DialogDescription>
            {row
              ? `${row.author.companyName || row.author.nickname || '（未命名账号）'} · 提交于 ${formatTime(row.createdAt)}`
              : ''}
          </DialogDescription>
        </DialogHeader>

        {row && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <StatusBadge status={CASE_STATUS[row.status]} />
              {row.reviewedAt && <span>{row.reviewedBy} 于 {formatTime(row.reviewedAt)} 处理</span>}
              {row.publishedAt && <span>发布于 {formatTime(row.publishedAt)}</span>}
            </div>

            {row.status === 'rejected' && row.rejectReason && (
              <div className="rounded-md border border-dashed p-2 text-sm">
                <span className="text-muted-foreground">驳回理由：</span>
                {row.rejectReason}
                {row.rejectNote && (
                  <div className="text-xs text-muted-foreground">{row.rejectNote}</div>
                )}
              </div>
            )}

            {row.body && <p className="whitespace-pre-wrap text-sm">{row.body}</p>}

            {/* 图给全，不只给封面：没看完就点通过等于没审 */}
            <div className="grid grid-cols-3 gap-2">
              {row.images.map((img, i) =>
                img.url ? (
                  <img
                    key={i}
                    src={img.url}
                    alt=""
                    className="w-full rounded object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div
                    key={i}
                    className="flex aspect-square items-center justify-center rounded bg-muted text-xs text-muted-foreground"
                  >
                    无法加载
                  </div>
                ),
              )}
            </div>

            {mode === 'reject' && (
              <div className="grid gap-1.5 border-t pt-3">
                <Label htmlFor="case-reject-reason">驳回理由</Label>
                <Input
                  id="case-reject-reason"
                  maxLength={60}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="例如：图片模糊 / 内容与家居行业无关 / 含联系方式"
                />
                <Label htmlFor="case-reject-note" className="mt-1">
                  补充说明（选填）
                </Label>
                <textarea
                  id="case-reject-note"
                  rows={2}
                  maxLength={200}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  className="w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
                />
                <p className="text-xs text-muted-foreground">
                  这两句话会原样显示给作者。驳回后他可以修改并重新提交。
                </p>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {row?.status === 'pending' && mode === 'view' && (
            <>
              <Button variant="outline" disabled={busy} onClick={() => setMode('reject')}>
                驳回
              </Button>
              <Button disabled={busy} onClick={() => void act(() => approveCase(row.id), '已发布')}>
                通过并发布
              </Button>
            </>
          )}
          {row?.status === 'pending' && mode === 'reject' && (
            <>
              <Button variant="ghost" disabled={busy} onClick={() => setMode('view')}>
                返回
              </Button>
              <Button variant="outline" disabled={busy} onClick={submitReject}>
                {busy ? '提交中…' : '确认驳回'}
              </Button>
            </>
          )}
          {row?.status !== 'pending' && (
            <Button variant="outline" onClick={onClose}>
              关闭
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 这个账号都发过什么。
 *
 * 判断一条案例是不是广告，最有力的依据往往不是这一条本身，而是同一个账号前面
 * 那几条长什么样——一条一条孤立地审，看不出「这个号在批量刷」。
 */
function AuthorDialog({ row, onClose }: { row: Case | null; onClose: () => void }) {
  const [items, setItems] = useState<Case[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!row) return
    setLoading(true)
    listUserCases(row.userId)
      .then((body) => setItems(body.items))
      .catch((err: unknown) => {
        toast.error(err instanceof Error ? err.message : String(err))
        setItems([])
      })
      .finally(() => setLoading(false))
  }, [row])

  return (
    <Dialog open={row !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>这个账号的案例</DialogTitle>
          <DialogDescription>
            {row
              ? `${row.author.companyName || row.author.nickname || '（未命名账号）'} · 共 ${items.length} 条`
              : ''}
          </DialogDescription>
        </DialogHeader>

        {loading && <Skeleton className="h-20 w-full" />}

        {!loading && (
          <ul className="space-y-2 text-sm">
            {items.map((item) => (
              <li key={item.id} className="flex items-center gap-2 rounded-md border p-2">
                <Thumb image={item.images[0]} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{item.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {formatTime(item.createdAt)} · {item.views} 次浏览
                  </div>
                </div>
                <StatusBadge status={CASE_STATUS[item.status as CaseStatus]} />
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
