import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { PaginationBar } from '@/components/pagination-bar'
import { SelectFilter } from '@/components/select-filter'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
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
import { formatTime, formatYuan } from '@/lib/format'
import { cn } from '@/lib/utils'

import {
  createProduct,
  deleteProduct,
  getProduct,
  listCategories,
  listProducts,
  setProductStatus,
  updateProduct,
} from './api'
import { ShopImageGroup } from './shop-image-group'
import type {
  ProductParam,
  ShopCategory,
  ShopImage,
  ShopProductDetail,
  ShopProductQuery,
  ShopProductRow,
} from './types'
import { usePagedList } from '@/lib/use-paged-list'

/**
 * 安玺·集 商品管理。
 *
 * 列表和编辑表单在同一个页面里切换，不另开路由：编辑表单有图集、详情图、参数三块，
 * 塞进抽屉太挤，而单开一条路由又要在 main.tsx 里维护 id 参数和「刷新页面回不到列表」
 * 的问题——这个后台里没有第二处需要深链接到一件商品。
 *
 * 服务端见 server/app/shop_admin.py 的 /products。
 */

// 与 server/app/models.py 的 MAX_PRODUCT_* 一致。库上的 CHECK 是最后一道，
// 这里先挡一次是为了让运营在传第 11 张图之前就知道超了
const MAX_IMAGES = 10
const MAX_DETAIL_IMAGES = 20
const MAX_PARAMS = 20

const BLANK: ShopProductQuery = { keyword: '', categoryId: '', status: '' }

const STATUS_OPTIONS = [
  { value: 'active', label: '在售' },
  { value: 'off', label: '已下架' },
] as const

const STATUS_META = {
  active: { label: '在售', tone: 'done' as const },
  off: { label: '已下架', tone: 'muted' as const },
}

export function ShopProductsPage() {
  const list = usePagedList<ShopProductRow, ShopProductQuery>(listProducts, BLANK)
  const [categories, setCategories] = useState<ShopCategory[]>([])
  // null = 看列表；'new' = 新建；其它 = 在编辑这个 id
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const loadCategories = useCallback(async () => {
    try {
      const body = await listCategories()
      setCategories(body.items)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void loadCategories()
  }, [loadCategories])

  if (editingId !== null) {
    return (
      <ProductEditor
        productId={editingId === 'new' ? null : editingId}
        categories={categories}
        onDone={(changed) => {
          setEditingId(null)
          if (changed) {
            list.reload()
            void loadCategories()
          }
        }}
      />
    )
  }

  const toggle = async (row: ShopProductRow) => {
    const next = row.status === 'active' ? 'off' : 'active'
    setBusy(row.id)
    try {
      await setProductStatus(row.id, next)
      toast.success(next === 'active' ? '已上架，用户现在就能买到' : '已下架')
      list.reload()
      void loadCategories()
    } catch (err) {
      // 上架失败通常是「没有商品图」或「分类已停用」，服务端的中文原因直接弹出来
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (row: ShopProductRow) => {
    if (!confirm(`确定删除「${row.title}」？删除不可撤销。\n\n只是暂时不卖的话请用「下架」。`))
      return
    setBusy(row.id)
    try {
      await deleteProduct(row.id)
      toast.success('已删除')
      list.reload()
      void loadCategories()
    } catch (err) {
      // 已经有订单的商品服务端回 409「删不掉，只能下架」
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const categoryOptions = categories.map((c) => ({
    value: c.id,
    label: c.status === 'active' ? c.name : `${c.name}（已停用）`,
  }))

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">商品管理</h1>
        <span className="text-sm text-muted-foreground">共 {list.total} 件</span>
        <div className="flex-1" />
        <Button size="sm" disabled={categories.length === 0} onClick={() => setEditingId('new')}>
          新建商品
        </Button>
      </header>

      {categories.length === 0 && (
        <p className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          还没有分类。商品必须归属一个分类，请先到「商品分类」建一个。
        </p>
      )}

      <div className="mb-3 flex flex-wrap gap-2">
        <Input
          className="w-56"
          placeholder="搜商品标题，回车查询"
          value={list.filters.keyword}
          onChange={(e) => list.setFilter('keyword', e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') list.search()
          }}
        />
        <SelectFilter
          className="w-40"
          placeholder="分类"
          value={list.filters.categoryId}
          options={categoryOptions}
          onChange={(v) => {
            list.setFilter('categoryId', v)
            list.search()
          }}
        />
        <SelectFilter
          className="w-32"
          placeholder="状态"
          value={list.filters.status}
          options={STATUS_OPTIONS}
          onChange={(v) => {
            list.setFilter('status', v)
            list.search()
          }}
        />
        <Button variant="ghost" size="sm" onClick={list.reset}>
          重置
        </Button>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-20">封面</TableHead>
              <TableHead>标题</TableHead>
              <TableHead className="w-32">分类</TableHead>
              <TableHead className="w-32">价格</TableHead>
              <TableHead className="w-24">状态</TableHead>
              <TableHead className="w-20">排序值</TableHead>
              <TableHead className="w-44">创建时间</TableHead>
              <TableHead className="w-48">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={8}>
                    <Skeleton className="h-6 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : list.items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  没有符合条件的商品
                </TableCell>
              </TableRow>
            ) : (
              list.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    {row.cover?.url ? (
                      <img
                        src={row.cover.url}
                        alt={row.title}
                        className="block size-12 rounded-md border border-border bg-muted object-cover"
                      />
                    ) : (
                      <div className="flex size-12 items-center justify-center rounded-md border border-border bg-muted text-xs text-muted-foreground">
                        无图
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="font-medium">{row.title}</TableCell>
                  <TableCell className="text-muted-foreground">{row.categoryName}</TableCell>
                  <TableCell>¥ {formatYuan(row.priceCents)}</TableCell>
                  <TableCell>
                    <StatusBadge status={STATUS_META[row.status]} />
                  </TableCell>
                  <TableCell>{row.sortOrder}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTime(row.createdAt)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={busy !== null}
                        onClick={() => setEditingId(row.id)}
                      >
                        编辑
                      </Button>
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={busy !== null}
                        onClick={() => void toggle(row)}
                      >
                        {row.status === 'active' ? '下架' : '上架'}
                      </Button>
                      <Button
                        size="xs"
                        variant="ghost"
                        className={cn('text-destructive hover:text-destructive')}
                        disabled={busy !== null}
                        onClick={() => void remove(row)}
                      >
                        删除
                      </Button>
                    </div>
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
    </div>
  )
}

// ---------------------------------------------------------------------------
// 编辑表单

/** 「12999.00」→ 1299900。整数运算 + 四舍五入，不让浮点碰钱 */
function yuanToCents(input: string): number | null {
  const text = input.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null
  const [yuan, fen = ''] = text.split('.')
  return Number(yuan) * 100 + Number(fen.padEnd(2, '0'))
}

const centsToYuan = (cents: number) => (cents / 100).toFixed(2)

interface EditorProps {
  /** null 表示新建 */
  productId: string | null
  categories: ShopCategory[]
  /** changed 为 true 时列表需要刷新 */
  onDone: (changed: boolean) => void
}

function ProductEditor({ productId, categories, onDone }: EditorProps) {
  const [loading, setLoading] = useState(productId !== null)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)

  const [status, setStatus] = useState<ShopProductDetail['status']>('off')
  const [categoryId, setCategoryId] = useState('')
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [price, setPrice] = useState('')
  const [sortOrder, setSortOrder] = useState('0')
  const [images, setImages] = useState<ShopImage[]>([])
  const [detailImages, setDetailImages] = useState<ShopImage[]>([])
  const [params, setParams] = useState<ProductParam[]>([])

  useEffect(() => {
    if (productId === null) {
      // 新建时默认落在第一个**启用中**的分类上，省一次点击
      setCategoryId(categories.find((c) => c.status === 'active')?.id ?? '')
      return
    }
    setLoading(true)
    getProduct(productId)
      .then((body) => {
        const item = body.item
        setStatus(item.status)
        setCategoryId(item.categoryId)
        setTitle(item.title)
        setSummary(item.summary)
        setPrice(centsToYuan(item.priceCents))
        setSortOrder(String(item.sortOrder))
        setImages(item.images)
        setDetailImages(item.detailImages)
        setParams(item.params)
      })
      .catch((err: unknown) => {
        toast.error(err instanceof Error ? err.message : String(err))
        onDone(false)
      })
      .finally(() => setLoading(false))
    // 依赖只跟 productId 走。categories 会在列表页刷新时换新数组、onDone 是行内箭头函数，
    // 把它们写进依赖会让这个 effect 在编辑过程中重跑，把运营填了一半的表单冲掉
  }, [productId])

  // 图已经传到 COS 了却没保存，直接关页面等于白传一趟。浏览器只允许问这一句
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  const submit = async () => {
    const cents = yuanToCents(price)
    if (cents === null || cents <= 0) {
      toast.warning('价格填成「12999」或「12999.00」这样，最多两位小数')
      return
    }
    const sort = Number(sortOrder)
    if (!Number.isInteger(sort)) {
      toast.warning('排序值要填整数')
      return
    }
    const blank = params.find((p) => !p.name.trim() || !p.value.trim())
    if (blank) {
      toast.warning('参数的名称和内容都要填，不用的那行请删掉')
      return
    }

    setSaving(true)
    try {
      const body = {
        categoryId,
        title: title.trim(),
        summary: summary.trim(),
        priceCents: cents,
        images: images.map((it) => it.key),
        detailImages: detailImages.map((it) => it.key),
        params: params.map((p) => ({ name: p.name.trim(), value: p.value.trim() })),
        sortOrder: sort,
      }
      if (productId) {
        await updateProduct(productId, body)
        toast.success('已保存')
      } else {
        await createProduct(body)
        // 新建出来是下架态，不说清楚运营会以为已经在卖了
        toast.success('已创建。商品当前是「已下架」，确认无误后在列表里点「上架」')
      }
      onDone(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const disabled = saving || uploading
  const activeCategories = categories.filter((c) => c.status === 'active' || c.id === categoryId)

  return (
    <div className="p-6">
      <header className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{productId ? '编辑商品' : '新建商品'}</h1>
        {productId && <StatusBadge status={STATUS_META[status]} />}
        <div className="flex-1" />
        <Button variant="outline" size="sm" disabled={disabled} onClick={() => onDone(false)}>
          返回列表
        </Button>
        <Button size="sm" disabled={disabled || !title.trim() || !categoryId} onClick={() => void submit()}>
          {saving ? '保存中…' : '保存'}
        </Button>
      </header>

      {status === 'active' && images.length === 0 && (
        <p className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          这件商品在售，但图集是空的。在售商品至少要有一张图，保存会被服务端拒绝——请先补图或先下架。
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-3 text-sm font-semibold">基本信息</h3>
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="product-title">标题</Label>
              <Input
                id="product-title"
                value={title}
                maxLength={60}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="商品名称，最多 60 字"
              />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="product-category">分类</Label>
              <select
                id="product-category"
                value={categoryId}
                onChange={(e) => setCategoryId(e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
              >
                <option value="">请选择</option>
                {activeCategories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.status === 'active' ? c.name : `${c.name}（已停用）`}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                商品必须归属一个分类，停用中的分类不能选
              </p>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="product-price">价格（元）</Label>
              <Input
                id="product-price"
                value={price}
                inputMode="decimal"
                onChange={(e) => setPrice(e.target.value)}
                placeholder="12999.00"
              />
              <p className="text-xs text-muted-foreground">
                最多两位小数。这是用户实际支付的金额，全场包邮、不另收运费
              </p>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="product-sort">排序值</Label>
              <Input
                id="product-sort"
                value={sortOrder}
                inputMode="numeric"
                onChange={(e) => setSortOrder(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">数字大的排在前面，可以填负数</p>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="product-summary">简介</Label>
              <textarea
                id="product-summary"
                value={summary}
                maxLength={500}
                rows={4}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="一段纯文本文案，显示在价格下方"
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
              />
              <p className="text-xs text-muted-foreground">
                {summary.length} / 500 字。详细介绍请做进「详情图」，这里只放一段短文案
              </p>
            </div>
          </div>
        </section>

        <ParamEditor params={params} disabled={disabled} onChange={setParams} />

        <ShopImageGroup
          title="图集"
          hint="详情页顶部的轮播图。第一张同时作为列表页的封面，选一张构图居中的"
          items={images}
          limit={MAX_IMAGES}
          disabled={saving}
          uploading={uploading}
          onUploadingChange={setUploading}
          onChange={setImages}
        />

        <ShopImageGroup
          title="详情图"
          hint="详情页正文，按顺序整幅铺开渲染。文字说明请直接做进图里"
          items={detailImages}
          limit={MAX_DETAIL_IMAGES}
          disabled={saving}
          uploading={uploading}
          onUploadingChange={setUploading}
          onChange={setDetailImages}
        />
      </div>
    </div>
  )
}

/**
 * 参数编辑器。自由名值对，不是预设字段集——家居品类差异太大，
 * 固定字段会让灯具留着一片「坐深」的空格。
 */
function ParamEditor({
  params,
  disabled,
  onChange,
}: {
  params: ProductParam[]
  disabled: boolean
  onChange: (params: ProductParam[]) => void
}) {
  const update = (index: number, patch: Partial<ProductParam>) => {
    onChange(params.map((p, i) => (i === index ? { ...p, ...patch } : p)))
  }

  const move = (index: number, step: number) => {
    const list = [...params]
    const target = index + step
    if (target < 0 || target >= list.length) return
    ;[list[index], list[target]] = [list[target], list[index]]
    onChange(list)
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">参数</h3>
        <span className="text-xs text-muted-foreground">
          {params.length} / {MAX_PARAMS} 条
        </span>
        <div className="flex-1" />
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || params.length >= MAX_PARAMS}
          onClick={() => onChange([...params, { name: '', value: '' }])}
        >
          添加一条
        </Button>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        展示在详情页的属性表，例如「材质 / 实木」「尺寸 / 200×90×75cm」。
        名称不能重复，顺序即展示顺序
      </p>

      {params.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">还没有参数</p>
      ) : (
        <div className="mt-3 space-y-2">
          {params.map((param, index) => (
            <div key={index} className="flex items-center gap-1.5">
              <Input
                className="w-32"
                value={param.name}
                maxLength={20}
                placeholder="材质"
                disabled={disabled}
                onChange={(e) => update(index, { name: e.target.value })}
              />
              <Input
                className="flex-1"
                value={param.value}
                maxLength={100}
                placeholder="实木"
                disabled={disabled}
                onChange={(e) => update(index, { value: e.target.value })}
              />
              <Button
                size="xs"
                variant="ghost"
                disabled={disabled || index === 0}
                onClick={() => move(index, -1)}
              >
                上移
              </Button>
              <Button
                size="xs"
                variant="ghost"
                disabled={disabled || index === params.length - 1}
                onClick={() => move(index, 1)}
              >
                下移
              </Button>
              <Button
                size="xs"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                disabled={disabled}
                onClick={() => onChange(params.filter((_, i) => i !== index))}
              >
                删除
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
