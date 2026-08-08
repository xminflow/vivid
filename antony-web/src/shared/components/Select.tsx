/**
 * 自定义下拉。
 *
 * 不用原生 <select>：把 appearance 清掉之后它只剩一条发丝线，跟旁边的文本框
 * 长得一模一样，看不出能展开；不清 appearance 又是各系统一套外观，跟页面不搭。
 * 而展开后的选项列表是浏览器渲染的，CSS 根本够不着——那才是最扎眼的地方。
 *
 * 代价是可访问性要自己兜：这里实现了 combobox/listbox 的 ARIA 角色、
 * 键盘操作（↑↓ 选、Enter 确认、Esc 关闭、Home/End 跳首尾）和焦点管理。
 *
 * 弹出层用 portal 挂到 body：FieldRow 的外层是 <label>，而 <label> 的内容模型
 * 只允许 phrasing content，把列表留在里面是无效 HTML；挂出去还能避免被祖先的
 * overflow 裁掉。代价是位置要自己算，所以页面一滚动就关掉，不做跟随。
 */
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

interface Props {
  value: string
  onChange: (v: string) => void
  options: readonly string[]
  placeholder?: string
}

/** 选项行高，用来估算弹出层高度、决定向下还是向上弹 */
const ROW_HEIGHT = 44
const MAX_VISIBLE = 7

export function SelectInput({ value, onChange, options, placeholder = '请选择' }: Props) {
  const [open, setOpen] = useState(false)
  // 键盘高亮项。与 value 分开：用键盘上下移动时还没选中，不该改表单的值
  const [active, setActive] = useState(-1)
  const [pos, setPos] = useState<{ top: number; left: number; width: number; up: boolean }>()

  const btnRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const listId = useId()

  function place() {
    const el = btnRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const height = Math.min(options.length, MAX_VISIBLE) * ROW_HEIGHT
    // 下方装不下就朝上弹，否则列表会被视口截断
    const up = r.bottom + height + 12 > window.innerHeight && r.top > height
    setPos({ top: up ? r.top - height - 8 : r.bottom + 8, left: r.left, width: r.width, up })
  }

  // 用 layout effect 定位：等到 effect 里再算的话，弹出层会先以 (0,0) 画一帧再跳过去
  useLayoutEffect(() => {
    if (open) place()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open) return

    const onDocPointerDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!btnRef.current?.contains(t) && !listRef.current?.contains(t)) setOpen(false)
    }
    // 滚动和窗口尺寸变化不做跟随，直接收起——跟随要在滚动里反复读 rect，
    // 长页面上很容易掉帧，而收起对用户来说是可预期的
    const onScrollOrResize = () => setOpen(false)

    document.addEventListener('pointerdown', onDocPointerDown)
    window.addEventListener('scroll', onScrollOrResize, true)
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      document.removeEventListener('pointerdown', onDocPointerDown)
      window.removeEventListener('scroll', onScrollOrResize, true)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [open])

  function openList() {
    setActive(Math.max(0, options.indexOf(value)))
    setOpen(true)
  }

  function pick(v: string) {
    onChange(v)
    setOpen(false)
    btnRef.current?.focus()
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        openList()
      }
      return
    }

    switch (e.key) {
      case 'Escape':
        e.preventDefault()
        setOpen(false)
        break
      case 'ArrowDown':
        e.preventDefault()
        setActive((i) => (i + 1) % options.length)
        break
      case 'ArrowUp':
        e.preventDefault()
        setActive((i) => (i - 1 + options.length) % options.length)
        break
      case 'Home':
        e.preventDefault()
        setActive(0)
        break
      case 'End':
        e.preventDefault()
        setActive(options.length - 1)
        break
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (options[active]) pick(options[active])
        break
    }
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-haspopup="listbox"
        // 外层 FieldRow 是个 <label>，点击标签文字会把点击转发给这个按钮。
        // 不拦下按钮自己那次点击的默认行为，转发会再触发一次 toggle，等于没反应
        onClick={(e) => {
          e.preventDefault()
          open ? setOpen(false) : openList()
        }}
        onKeyDown={onKeyDown}
        className="group flex w-full cursor-pointer items-center justify-between gap-2 text-left text-[15px]"
      >
        <span
          className={`transition-colors ${
            value ? 'text-nero' : 'text-grigio-chiaro group-hover:text-grigio'
          }`}
        >
          {value || placeholder}
        </span>
        <span
          aria-hidden="true"
          className={`shrink-0 text-[10px] transition-all duration-200 group-hover:text-nero ${
            open ? 'rotate-180 text-nero' : 'text-grigio'
          }`}
        >
          ▼
        </span>
      </button>

      {open &&
        pos &&
        createPortal(
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            tabIndex={-1}
            style={{
              position: 'fixed',
              top: pos.top,
              left: pos.left,
              width: pos.width,
              maxHeight: MAX_VISIBLE * ROW_HEIGHT,
            }}
            className="z-50 overflow-y-auto border border-hairline bg-bianco shadow-[0_12px_40px_rgba(17,17,17,0.14)]"
          >
            {options.map((o, i) => (
              <li
                key={o}
                role="option"
                aria-selected={o === value}
                // 用 pointerdown 而不是 click：pointerdown 早于 blur，
                // 点选项时按钮先失焦会让弹出层在 click 到达前就被收起
                onPointerDown={(e) => {
                  e.preventDefault()
                  pick(o)
                }}
                onMouseEnter={() => setActive(i)}
                className={`flex cursor-pointer items-center justify-between px-4 text-[14px] transition-colors ${
                  i === active ? 'bg-sfondo' : ''
                } ${o === value ? 'text-nero' : 'text-grigio'}`}
                style={{ height: ROW_HEIGHT }}
              >
                {o}
                {o === value && (
                  <span aria-hidden="true" className="text-[11px]">
                    ●
                  </span>
                )}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </>
  )
}
