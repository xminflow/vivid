import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { getHomeMedia, saveHomeMedia, uploadHomeImage } from './api'
import type { HomeMediaItem, HomeSlot } from './types'

/**
 * 首页配图管理。
 *
 * 三个位置各自独立编辑、独立保存：一次保存就是整组替换，数组顺序即首页展示顺序
 * （服务端接口见 server/app/admin.py 的 /home-media）。
 *
 * 上传和保存是分开的两步：选完图先传到 COS 拿对象键，点「保存」才写进配置。
 * 这样运营可以先传几张、调完顺序再落地，中途关掉页面不会把首页改成一半的样子。
 */

// 位置的展示信息。id 与 server/app/models.py 的 HOME_SLOTS 逐字一致
const SLOTS: { id: HomeSlot; title: string; hint: string }[] = [
  {
    id: 'hero',
    title: '首屏画廊',
    hint: '首页最上方自动轮播的整屏实拍。品牌标语压在图上，选图时留意下半部分别太花',
  },
  {
    id: 'showroom',
    title: '展厅实拍',
    hint: '「展厅预约」下面左右滑动的一组图，顺序就是参观动线的顺序',
  },
  {
    id: 'activity',
    title: '近期活动',
    hint: '整张海报铺在首页底部，页面不再另配文字，所以文案要直接做在图里',
  },
]

// 与服务端 cos.MAX_UPLOAD_BYTES 一致。先在浏览器里挡一次，省一趟白跑的上传
const MAX_BYTES = 10 * 1024 * 1024
// 服务端按文件头认这三种（cos.sniff_image_ext），这里的 accept 只是给选择框做过滤
const ACCEPT = 'image/jpeg,image/png,image/webp'

type SlotState = Record<HomeSlot, HomeMediaItem[]>

const EMPTY: SlotState = { hero: [], showroom: [], activity: [] }

const keysOf = (list: HomeMediaItem[]) => list.map((it) => it.key).join('|')

export function HomeMediaPage() {
  const [loading, setLoading] = useState(true)
  // 正在上传或保存的位置。同一组的按钮在此期间禁用，别的组不受影响
  const [busy, setBusy] = useState<HomeSlot | null>(null)
  const [limits, setLimits] = useState<Record<HomeSlot, number>>({
    hero: 0,
    showroom: 0,
    activity: 0,
  })
  // draft 是编辑中的状态，saved 是服务端当前的状态，两者比对得出「有没有未保存的改动」
  const [draft, setDraft] = useState<SlotState>(EMPTY)
  const [saved, setSaved] = useState<SlotState>(EMPTY)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const body = await getHomeMedia()
      const next: SlotState = { hero: [], showroom: [], activity: [] }
      for (const slot of SLOTS) next[slot.id] = body.slots[slot.id] ?? []
      setDraft(next)
      setSaved(next)
      setLimits(body.limits)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const isDirty = (slot: HomeSlot) => keysOf(draft[slot]) !== keysOf(saved[slot])
  const dirtyAnywhere = SLOTS.some((slot) => isDirty(slot.id))

  // 图已经传到 COS 了却没保存，直接关页面等于白传一趟。浏览器只允许问这一句
  useEffect(() => {
    if (!dirtyAnywhere) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirtyAnywhere])

  const addFiles = async (slot: HomeSlot, files: File[]) => {
    const room = limits[slot] - draft[slot].length
    if (files.length > room) {
      toast.warning(`这个位置最多放 ${limits[slot]} 张，还能再加 ${room} 张`)
      return
    }
    const tooBig = files.find((f) => f.size > MAX_BYTES)
    if (tooBig) {
      toast.warning(`「${tooBig.name}」超过 ${MAX_BYTES / 1024 / 1024}MB，请先压缩`)
      return
    }

    setBusy(slot)
    try {
      // 一张一张传：顺序就是运营选中的顺序，并发上传会打乱
      for (const file of files) {
        const { key, url } = await uploadHomeImage(file)
        setDraft((prev) => ({ ...prev, [slot]: [...prev[slot], { id: key, key, url }] }))
      }
      toast.success('图片已上传，点「保存」后才会在小程序生效')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const move = (slot: HomeSlot, index: number, step: number) => {
    setDraft((prev) => {
      const list = [...prev[slot]]
      const target = index + step
      if (target < 0 || target >= list.length) return prev
      ;[list[index], list[target]] = [list[target], list[index]]
      return { ...prev, [slot]: list }
    })
  }

  const remove = (slot: HomeSlot, index: number) => {
    setDraft((prev) => ({ ...prev, [slot]: prev[slot].filter((_, i) => i !== index) }))
  }

  const revert = (slot: HomeSlot) => {
    setDraft((prev) => ({ ...prev, [slot]: saved[slot] }))
  }

  const save = async (slot: HomeSlot) => {
    setBusy(slot)
    try {
      const list = draft[slot]
      await saveHomeMedia(
        slot,
        list.map((it) => it.key),
      )
      setSaved((prev) => ({ ...prev, [slot]: list }))
      toast.success('已保存，用户下次打开小程序就是新图')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  // 有图但拼不出地址 = 服务端没配 COS。要说出来，否则运营会以为图丢了
  const missingUrl = SLOTS.some((slot) => draft[slot.id].some((it) => it.url === null))

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h2 className="text-lg font-semibold">首页图片</h2>
        <span className="text-sm text-muted-foreground">
          改完点各组自己的「保存」，用户下次打开小程序时生效，不用发版
        </span>
      </header>

      {missingUrl && (
        <p className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          部分图片无法显示：服务端未配置 COS（COS_SECRET_ID / COS_BUCKET 等）
        </p>
      )}

      {loading ? (
        <div className="space-y-4">
          {SLOTS.map((slot) => (
            <Skeleton key={slot.id} className="h-40 w-full" />
          ))}
        </div>
      ) : (
        SLOTS.map((slot) => (
          <SlotSection
            key={slot.id}
            slot={slot}
            items={draft[slot.id]}
            limit={limits[slot.id]}
            dirty={isDirty(slot.id)}
            busy={busy === slot.id}
            disabled={busy !== null}
            onAdd={(files) => void addFiles(slot.id, files)}
            onMove={(index, step) => move(slot.id, index, step)}
            onRemove={(index) => remove(slot.id, index)}
            onRevert={() => revert(slot.id)}
            onSave={() => void save(slot.id)}
          />
        ))
      )}
    </div>
  )
}

interface SlotSectionProps {
  slot: { id: HomeSlot; title: string; hint: string }
  items: HomeMediaItem[]
  limit: number
  dirty: boolean
  busy: boolean
  disabled: boolean
  onAdd: (files: File[]) => void
  onMove: (index: number, step: number) => void
  onRemove: (index: number) => void
  onRevert: () => void
  onSave: () => void
}

function SlotSection({
  slot,
  items,
  limit,
  dirty,
  busy,
  disabled,
  onAdd,
  onMove,
  onRemove,
  onRevert,
  onSave,
}: SlotSectionProps) {
  const picker = useRef<HTMLInputElement>(null)

  return (
    <section className="mb-4 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{slot.title}</h3>
        <span className="text-xs text-muted-foreground">
          {items.length} / {limit} 张
        </span>
        {dirty && (
          <span className="rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-400">
            未保存
          </span>
        )}
        <div className="flex-1" />
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || items.length >= limit}
          onClick={() => picker.current?.click()}
        >
          添加图片
        </Button>
        <Button size="sm" variant="ghost" disabled={disabled || !dirty} onClick={onRevert}>
          撤销
        </Button>
        <Button size="sm" disabled={disabled || !dirty} onClick={onSave}>
          {busy ? '处理中…' : '保存'}
        </Button>
      </div>

      <p className="mt-1.5 text-xs text-muted-foreground">{slot.hint}</p>

      {/* 清空是「回到小程序里写死的兜底图」，不是「首页这块不显示」。
          这个语义不摆出来，运营会以为自己把首页删空了 */}
      {dirty && items.length === 0 && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          保存后这一组将没有后台配置，小程序会改用代码里写死的兜底图。
        </p>
      )}

      {/* 选择框只作为点击入口，样式全交给上面的按钮 */}
      <input
        ref={picker}
        type="file"
        hidden
        accept={ACCEPT}
        multiple={limit > 1}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          // 清掉值，否则连着选同一个文件不会再触发 change
          e.target.value = ''
          if (files.length) onAdd(files)
        }}
      />

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          还没配图，小程序这一块用的是代码里写死的兜底图。传图并保存后会改用这里配的。
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-3">
          {items.map((item, index) => (
            <figure
              key={item.key}
              className="w-40 overflow-hidden rounded-md border border-border"
            >
              {item.url ? (
                <img
                  src={item.url}
                  alt={`${slot.title}第 ${index + 1} 张`}
                  className="block h-28 w-40 bg-muted object-cover"
                />
              ) : (
                /* 拼不出地址时不装作没有这张图：配置里确实有，只是显示不出来 */
                <div
                  title={item.key}
                  className="flex h-28 w-40 items-center justify-center bg-muted px-2 text-center text-xs text-amber-700 dark:text-amber-400"
                >
                  地址生成失败
                </div>
              )}
              <figcaption className="flex items-center gap-0.5 border-t border-border px-1.5 py-1">
                <span className="w-4 text-xs text-muted-foreground">{index + 1}</span>
                {/* 不引拖拽库，上移下移够用且不会误触 */}
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={disabled || index === 0}
                  onClick={() => onMove(index, -1)}
                >
                  上移
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={disabled || index === items.length - 1}
                  onClick={() => onMove(index, 1)}
                >
                  下移
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  className={cn('text-destructive hover:text-destructive')}
                  disabled={disabled}
                  onClick={() => onRemove(index)}
                >
                  移除
                </Button>
              </figcaption>
            </figure>
          ))}
        </div>
      )}
    </section>
  )
}
