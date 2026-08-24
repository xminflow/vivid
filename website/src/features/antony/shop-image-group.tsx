import { useRef } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'

import { uploadProductImage } from './api'
import type { ShopImage } from './types'

/**
 * 一组有序图片的上传与排序。商品的「图集」和「详情图」用的是同一套操作，
 * 差别只有上限和文案，所以抽成一个组件而不是在编辑页里写两遍。
 *
 * 上传和保存是分开的两步：选完图先传到 COS 拿对象键，点商品表单的「保存」才写进库。
 * 与首页配图（home-media-page.tsx）一致，这里也不引拖拽库——上移下移够用且不会误触。
 */

// 与服务端 cos.MAX_UPLOAD_BYTES 一致。先在浏览器里挡一次，省一趟白跑的上传
const MAX_BYTES = 10 * 1024 * 1024
// 服务端按文件头认这三种（cos.sniff_image_ext），accept 只是给选择框做过滤
const ACCEPT = 'image/jpeg,image/png,image/webp'
// ⚠️ 手机直出的 HEIC 不支持（服务端认不出这个文件头），得先转成 JPG。
// 写出来是因为选择框的过滤只在「选文件」那一刻生效，拖拽或手动切成「所有文件」都能绕过
const FORMATS = 'JPG / PNG / WebP'

interface Props {
  title: string
  hint: string
  items: ShopImage[]
  limit: number
  /** 上传中或表单正在保存时为 true，整组按钮禁用 */
  disabled: boolean
  uploading: boolean
  onUploadingChange: (uploading: boolean) => void
  onChange: (items: ShopImage[]) => void
}

export function ShopImageGroup({
  title,
  hint,
  items,
  limit,
  disabled,
  uploading,
  onUploadingChange,
  onChange,
}: Props) {
  const picker = useRef<HTMLInputElement>(null)

  const addFiles = async (files: File[]) => {
    const room = limit - items.length
    if (files.length > room) {
      toast.warning(`${title}最多 ${limit} 张，还能再加 ${room} 张`)
      return
    }
    const tooBig = files.find((f) => f.size > MAX_BYTES)
    if (tooBig) {
      toast.warning(`「${tooBig.name}」超过 ${MAX_BYTES / 1024 / 1024}MB，请先压缩`)
      return
    }

    onUploadingChange(true)
    try {
      // 一张一张传：顺序就是运营选中的顺序，并发上传会打乱
      const uploaded: ShopImage[] = []
      for (const file of files) {
        const { key, url } = await uploadProductImage(file)
        uploaded.push({ key, url })
      }
      onChange([...items, ...uploaded])
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      onUploadingChange(false)
    }
  }

  const move = (index: number, step: number) => {
    const list = [...items]
    const target = index + step
    if (target < 0 || target >= list.length) return
    ;[list[index], list[target]] = [list[target], list[index]]
    onChange(list)
  }

  const busy = disabled || uploading

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-xs text-muted-foreground">
          {items.length} / {limit} 张
        </span>
        <div className="flex-1" />
        <Button
          size="sm"
          variant="outline"
          disabled={busy || items.length >= limit}
          onClick={() => picker.current?.click()}
        >
          {uploading ? '上传中…' : '添加图片'}
        </Button>
      </div>

      <p className="mt-1.5 text-xs text-muted-foreground">{hint}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        支持 {FORMATS}，单张不超过 {MAX_BYTES / 1024 / 1024}MB。手机拍的 HEIC 要先转成 JPG
      </p>

      <input
        ref={picker}
        type="file"
        hidden
        multiple
        accept={ACCEPT}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          // 清掉值，否则连着选同一个文件不会再触发 change
          e.target.value = ''
          if (files.length) void addFiles(files)
        }}
      />

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">还没有图片</p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-3">
          {items.map((item, index) => (
            <figure key={item.key} className="w-40 overflow-hidden rounded-md border border-border">
              {item.url ? (
                <img
                  src={item.url}
                  alt={`${title}第 ${index + 1} 张`}
                  className="block h-28 w-40 bg-muted object-cover"
                />
              ) : (
                /* 拼不出地址时不装作没有这张图：库里确实有，只是显示不出来 */
                <div
                  title={item.key}
                  className="flex h-28 w-40 items-center justify-center bg-muted px-2 text-center text-xs text-amber-700 dark:text-amber-400"
                >
                  地址生成失败
                </div>
              )}
              <figcaption className="flex items-center gap-0.5 border-t border-border px-1.5 py-1">
                <span className="w-4 text-xs text-muted-foreground">{index + 1}</span>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={busy || index === 0}
                  onClick={() => move(index, -1)}
                >
                  上移
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={busy || index === items.length - 1}
                  onClick={() => move(index, 1)}
                >
                  下移
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  className="text-destructive hover:text-destructive"
                  disabled={busy}
                  onClick={() => onChange(items.filter((_, i) => i !== index))}
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
