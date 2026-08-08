import { useEffect, useRef, useState } from 'react'

/**
 * 元素滚进视口时给一次入场动画。
 *
 * 用 IntersectionObserver 而不是监听 scroll：后者每帧都要读一次 getBoundingClientRect，
 * 会强制同步布局，长页面上滚动很容易掉帧。
 * 触发一次就断开——出场再入场重复播放，翻回上文时会闪。
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(rootMargin = '-10% 0px') {
  const ref = useRef<T>(null)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el || shown) return

    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true)
          io.disconnect()
        }
      },
      { rootMargin },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [rootMargin, shown])

  return { ref, className: shown ? 'reveal reveal-in' : 'reveal' }
}
