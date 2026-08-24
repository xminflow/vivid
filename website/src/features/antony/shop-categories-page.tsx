import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

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

import {
  activateCategoryProducts,
  createCategory,
  listCategories,
  setCategoryStatus,
  updateCategory,
} from './api'
import type { ShopCategory } from './types'

/**
 * 安玺·集 分类管理。
 *
 * 分类**只能停用、不能删除**：商品的分类是必填的，真删掉就得回答「这些商品归谁」。
 * 而停用的语义是干净的——分类从小程序和新建商品的下拉里消失，其下商品一并下架。
 *
 * 停用是这个后台里最容易误操作的一步：一次点击可能下架十几件在售商品，而且
 * **重新启用不会自动恢复**。所以列表里必须显示商品数，二次确认必须把件数说出来。
 * 恢复走「批量上架」，是一个显式动作。
 *
 * 服务端见 server/app/shop_admin.py 的 /categories。
 */

const STATUS_META = {
  active: { label: '启用中', tone: 'done' as const },
  disabled: { label: '已停用', tone: 'muted' as const },
}

export function ShopCategoriesPage() {
  const [items, setItems] = useState<ShopCategory[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<ShopCategory | null>(null)
  // 正在提交的分类 id。同一行的按钮在此期间禁用，避免连点出两次批量下架
  const [busy, setBusy] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const body = await listCategories()
      setItems(body.items)
    } catch (err) {
      setItems([])
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const toggle = async (row: ShopCategory) => {
    const next = row.status === 'active' ? 'disabled' : 'active'
    if (next === 'disabled') {
      // 件数写进确认文案里，不能只说「确定停用吗」——运营看不见影响面就等于没确认
      const tail =
        row.activeCount > 0
          ? `该分类下 ${row.activeCount} 件在售商品会一并下架，重新启用不会自动恢复。`
          : '该分类下没有在售商品。'
      if (!confirm(`确定停用「${row.name}」？\n\n${tail}`)) return
    }

    setBusy(row.id)
    try {
      const body = await setCategoryStatus(row.id, next)
      if (next === 'disabled') {
        toast.success(
          body.takenDown > 0 ? `已停用，同时下架了 ${body.takenDown} 件商品` : '已停用',
        )
      } else {
        // 启用不恢复商品，这一点必须当场说清楚，否则运营会以为商品自己回来了
        toast.success('已启用。其下商品仍是下架状态，要恢复请点「批量上架」')
      }
      await reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const activateAll = async (row: ShopCategory) => {
    const offCount = row.productCount - row.activeCount
    if (!confirm(`把「${row.name}」下 ${offCount} 件已下架的商品全部上架？`)) return

    setBusy(row.id)
    try {
      const body = await activateCategoryProducts(row.id)
      // 跳过的件数要说出来。不说的话运营会以为全上架了，而那几件没封面的还躺着
      toast.success(
        body.skippedWithoutImage > 0
          ? `已上架 ${body.activated} 件，另有 ${body.skippedWithoutImage} 件没有商品图，需要先补图`
          : `已上架 ${body.activated} 件`,
      )
      await reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">商品分类</h1>
        <span className="text-sm text-muted-foreground">共 {items.length} 个</span>
        <div className="flex-1" />
        <Button size="sm" onClick={() => setCreating(true)}>
          新建分类
        </Button>
      </header>

      <p className="mb-4 text-xs text-muted-foreground">
        排序值大的排在前面，小程序里的分类顺序按它来。分类只能停用不能删除；停用会把
        该分类下的在售商品一并下架，且重新启用不会自动恢复。
      </p>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead className="w-24">排序值</TableHead>
              <TableHead className="w-32">商品</TableHead>
              <TableHead className="w-24">状态</TableHead>
              <TableHead className="w-44">创建时间</TableHead>
              <TableHead className="w-64">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={6}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  还没有分类。商品必须归属一个分类，先建一个再去上架商品
                </TableCell>
              </TableRow>
            ) : (
              items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">{row.name}</TableCell>
                  <TableCell>{row.sortOrder}</TableCell>
                  <TableCell className="text-muted-foreground">
                    在售 {row.activeCount} / 共 {row.productCount}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={STATUS_META[row.status]} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTime(row.createdAt)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={busy !== null}
                        onClick={() => setEditing(row)}
                      >
                        编辑
                      </Button>
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={busy !== null}
                        onClick={() => void toggle(row)}
                      >
                        {row.status === 'active' ? '停用' : '启用'}
                      </Button>
                      {/* 只在「启用中且确实有下架商品」时出现：另外两种情况点了也没事发生 */}
                      {row.status === 'active' && row.productCount > row.activeCount && (
                        <Button
                          size="xs"
                          variant="ghost"
                          disabled={busy !== null}
                          onClick={() => void activateAll(row)}
                        >
                          批量上架
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <CategoryDialog
        key={editing?.id ?? 'create'}
        open={creating || editing !== null}
        category={editing}
        onOpenChange={(open) => {
          if (open) return
          setCreating(false)
          setEditing(null)
        }}
        onSaved={reload}
      />
    </div>
  )
}

/** 新建和编辑共用一个弹窗：两者的字段完全一样，分成两个组件只是抄一遍。 */
function CategoryDialog({
  open,
  category,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  category: ShopCategory | null
  onOpenChange: (open: boolean) => void
  onSaved: () => Promise<void>
}) {
  const [name, setName] = useState(category?.name ?? '')
  const [sortOrder, setSortOrder] = useState(String(category?.sortOrder ?? 0))
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const sort = Number(sortOrder)
    if (!Number.isInteger(sort)) {
      toast.warning('排序值要填整数')
      return
    }

    setBusy(true)
    try {
      const body = { name: name.trim(), sortOrder: sort }
      if (category) await updateCategory(category.id, body)
      else await createCategory(body)
      toast.success('已保存')
      onOpenChange(false)
      await onSaved()
    } catch (err) {
      // 重名时服务端回 409，@/lib/api 把 detail 抛成 Error，这里原样弹出
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{category ? '编辑分类' : '新建分类'}</DialogTitle>
          <DialogDescription>
            分类名会直接显示在小程序顶部，短一点更好排（最多 20 字）。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="category-name">名称</Label>
            <Input
              id="category-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="沙发 / 灯具 / 餐桌…"
            />
            <p className="text-xs text-muted-foreground">不能与已有分类重名</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="category-sort">排序值</Label>
            <Input
              id="category-sort"
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value)}
              inputMode="numeric"
            />
            <p className="text-xs text-muted-foreground">数字大的排在前面，可以填负数</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={busy || !name.trim()}>
            {busy ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
