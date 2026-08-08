/**
 * 图片上传组：一组一个九宫格，选完立刻传 COS，成功后只把对象键交给表单。
 *
 * 选完就传（而不是攒到提交时一起传）有两个原因：一是几张大图串行传要十几秒，
 * 压在提交按钮后面用户会以为卡死；二是传失败可以单张重试，不必整份表单重来。
 */
import { useEffect, useRef, useState } from 'react'
import { ACCEPT_ATTR, uploadImage } from '@/shared/upload'

type ItemStatus = 'uploading' | 'done' | 'error'

interface Item {
  /** 本地临时 id，只用于列表 key 和删除定位，不提交给服务端 */
  id: string
  previewUrl: string
  status: ItemStatus
  /** 上传成功后服务端签发的 COS 对象键，提交的就是它 */
  key?: string
  error?: string
}

interface Props {
  label: string
  scene: string
  max: number
  /** 只在成功的键集合变化时回调，上传中和失败的不进表单 */
  onChange: (keys: string[]) => void
}

let seq = 0

export function ImageUploader({ label, scene, max, onChange }: Props) {
  const [items, setItems] = useState<Item[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  // 卸载时要 revoke 的是「当时」的列表，所以用 ref 跟着最新值走。
  // 直接在空依赖的 effect 里读 items 会捕获首次渲染的空数组，
  // 清理函数跑的时候一张也 revoke 不到——看着有清理，其实全泄漏了
  const itemsRef = useRef(items)
  itemsRef.current = items

  // 预览用的 blob URL 不主动释放就一直占着内存。SPA 里路由切走并不算页面关闭，
  // 浏览器不会替我们回收
  useEffect(() => {
    return () => {
      itemsRef.current.forEach((it) => URL.revokeObjectURL(it.previewUrl))
    }
  }, [])

  function publish(next: Item[]) {
    setItems(next)
    onChange(next.filter((it) => it.status === 'done' && it.key).map((it) => it.key as string))
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return

    const room = max - items.length
    const picked = Array.from(files).slice(0, Math.max(room, 0))
    if (!picked.length) return

    const added: Item[] = picked.map((f) => ({
      id: `u${++seq}`,
      previewUrl: URL.createObjectURL(f),
      status: 'uploading',
    }))

    // 先把占位铺出来，用户立刻看到选中了几张，再逐张传
    let current = [...items, ...added]
    publish(current)

    await Promise.all(
      picked.map(async (file, i) => {
        const id = added[i].id
        try {
          const key = await uploadImage(file, scene)
          current = current.map((it) => (it.id === id ? { ...it, status: 'done', key } : it))
        } catch (e) {
          const msg = e instanceof Error ? e.message : '上传失败'
          current = current.map((it) =>
            it.id === id ? { ...it, status: 'error', error: msg } : it,
          )
        }
        publish(current)
      }),
    )

    // 清空 input，否则再选同一个文件不会触发 change
    if (inputRef.current) inputRef.current.value = ''
  }

  function remove(id: string) {
    const target = items.find((it) => it.id === id)
    if (target) URL.revokeObjectURL(target.previewUrl)
    publish(items.filter((it) => it.id !== id))
  }

  return (
    <div>
      <span className="text-[13px] tracking-meta text-grigio">
        {label}
        <span className="ml-2 text-grigio-chiaro">
          {items.length}/{max}
        </span>
      </span>

      <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {items.map((it) => (
          <figure key={it.id} className="relative aspect-square overflow-hidden bg-bianco">
            <img src={it.previewUrl} alt="" className="h-full w-full object-cover" />

            {it.status !== 'done' && (
              <figcaption className="absolute inset-0 flex items-center justify-center bg-nero/60 px-1 text-center text-[11px] text-su-foto">
                {it.status === 'uploading' ? '上传中…' : it.error}
              </figcaption>
            )}

            <button
              type="button"
              onClick={() => remove(it.id)}
              aria-label="删除这张图片"
              className="absolute top-1 right-1 flex h-6 w-6 items-center justify-center bg-nero/70 text-[14px] leading-none text-su-foto"
            >
              ×
            </button>
          </figure>
        ))}

        {items.length < max && (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="flex aspect-square items-center justify-center border border-dashed border-hairline text-[22px] text-grigio-chiaro transition-colors hover:border-nero hover:text-nero"
            aria-label={`添加${label}`}
          >
            +
          </button>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        multiple
        className="hidden"
        onChange={(e) => void handleFiles(e.target.files)}
      />
    </div>
  )
}
