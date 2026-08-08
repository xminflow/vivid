/**
 * 首页 = 首屏画廊 + 展厅实拍 + 活动预告。对应小程序 pages/index。
 *
 * 展厅只有一个，六张实拍是同一个空间的六个视角。两套排布：
 *   手机  一屏一张横向滑动，和小程序的 swiper 一致——竖屏上并排铺只会把每张压得很小
 *   桌面  铺成网格，一眼看完整个展厅
 * 横滑用原生 CSS scroll-snap 而不是引一个轮播库：触摸惯性、回弹、无障碍都是
 * 浏览器现成的，也不用为一个画廊多背一份依赖。
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useReveal } from '@/shared/useReveal'
import { HeroGallery } from './HeroGallery'
import { activity, showroom } from './content'

function SectionHead({ title, en }: { title: string; en: string }) {
  return (
    <div className="flex items-baseline justify-between">
      <h2 className="text-[22px] tracking-display sm:text-[30px]">{title}</h2>
      <span className="text-[13px] tracking-meta text-grigio-chiaro">{en}</span>
    </div>
  )
}

/** 自动切换间隔，与首屏 hero 保持一致 */
const AUTOPLAY_INTERVAL = 5000
/** 手动操作后暂停自动播放的时长。太短的话刚滑到想看的那张就被切走 */
const AUTOPLAY_PAUSE = 8000

/**
 * 展厅实拍画廊。图不再各自链到某一间，整段共用下面那一个预约入口。
 *
 * 淡入放在整个画廊上而不是每张图上：横滑时后面几张一直在视口外，逐张淡入会变成
 * 「滑过去了还要等它慢慢显形」。
 */
function ShowroomGallery() {
  const reveal = useReveal<HTMLDivElement>()
  const scroller = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState(0)
  // 手动操作后暂停自动播放到这个时刻为止
  const pausedUntil = useRef(0)

  // 滚到哪张就亮哪个页码。桌面端是网格不滚动，scrollLeft 恒为 0，页码本身也是隐藏的
  function syncActive() {
    const el = scroller.current
    if (!el) return
    setActive(Math.round(el.scrollLeft / el.clientWidth))
  }

  function scrollTo(i: number, smooth = true) {
    const el = scroller.current
    if (!el) return
    el.scrollTo({ left: i * el.clientWidth, behavior: smooth ? 'smooth' : 'auto' })
  }

  /** 用户碰过之后先别自动切，让他把当前这张看完 */
  function pauseAutoplay() {
    pausedUntil.current = Date.now() + AUTOPLAY_PAUSE
  }

  useEffect(() => {
    // 动效敏感的用户不该被动画干扰，和 HeroGallery 一个判断
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const timer = window.setInterval(() => {
      const el = scroller.current
      if (!el) return
      // 桌面端是网格，压根不滚动（scrollWidth == clientWidth），直接跳过
      if (el.scrollWidth <= el.clientWidth) return
      if (Date.now() < pausedUntil.current) return

      const current = Math.round(el.scrollLeft / el.clientWidth)
      const next = (current + 1) % showroom.images.length
      // 从最后一张回到第一张时不要平滑滚：那是整整五屏的距离，
      // 会看到一段很长的倒卷，还不如直接跳回去干净
      scrollTo(next, next !== 0)
    }, AUTOPLAY_INTERVAL)

    return () => window.clearInterval(timer)
  }, [])

  return (
    <div ref={reveal.ref} className={`${reveal.className} mt-10`}>
      <div
        ref={scroller}
        onScroll={syncActive}
        // 用 pointerdown 判断「用户在操作」而不是 onScroll：自动播放本身也会触发
        // onScroll，拿它当交互信号的话自动播放会把自己一直暂停下去
        onPointerDown={pauseAutoplay}
        className="no-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto sm:grid sm:snap-none sm:grid-cols-2 sm:gap-5 sm:overflow-visible lg:grid-cols-3"
      >
        {showroom.images.map((src, i) => (
          // w-full 是相对 flex 容器的宽度，也就是一屏一张；桌面端交给 grid 分列
          <div key={src} className="group w-full shrink-0 snap-start overflow-hidden bg-nero sm:w-auto">
            <div className="relative aspect-[4/5] overflow-hidden">
              <img
                src={src}
                alt={`${showroom.name}实拍 ${i + 1}`}
                // 第一张要立刻出来，它在首屏往下一屏的位置，用户滚过来就得看到
                loading={i === 0 ? 'eager' : 'lazy'}
                decoding="async"
                className="h-full w-full object-cover transition-transform duration-[900ms] ease-out group-hover:scale-[1.04]"
              />
            </div>
          </div>
        ))}
      </div>

      {/* 页码只在手机上出现：桌面端六张全铺开了，再给页码反而多余 */}
      <div className="mt-4 flex gap-2 sm:hidden" role="tablist" aria-label="展厅实拍">
        {showroom.images.map((src, i) => (
          <button
            key={src}
            role="tab"
            aria-selected={i === active}
            aria-label={`第 ${i + 1} 张`}
            onClick={() => {
              pauseAutoplay()
              scrollTo(i)
            }}
            className={`h-[2px] transition-all duration-300 ${
              i === active ? 'w-10 bg-nero' : 'w-5 bg-hairline'
            }`}
          />
        ))}
      </div>
    </div>
  )
}

export function HomePage() {
  const activityReveal = useReveal()

  return (
    <>
      <HeroGallery />

      <section className="mx-auto max-w-[1440px] px-5 py-20 sm:px-10 sm:py-28">
        <SectionHead title="展厅预约" en="Visit" />

        <ShowroomGallery />

        {/* 信息条 + 唯一的预约入口：展厅只有一个，落款也只需要一处 */}
        <div className="mt-10 flex flex-wrap items-end justify-between gap-6 border-t border-hairline pt-8">
          <div>
            <h3 className="text-[19px] tracking-display">
              {showroom.name} <span className="text-grigio-chiaro">{showroom.en}</span>
            </h3>
            <p className="mt-2 text-[12px] tracking-meta text-grigio">{showroom.hours}</p>
            <p className="mt-1 text-[12px] tracking-meta text-grigio">{showroom.floor}</p>
          </div>
          <Link
            to="/booking"
            className="inline-block bg-nero px-12 py-4 text-[14px] tracking-meta text-bianco transition-opacity hover:opacity-85"
          >
            预约参观
          </Link>
        </div>
      </section>

      {/* 活动预告：整张海报直接铺出来，文案都在图里，页面不再另起一套字 */}
      <section
        ref={activityReveal.ref}
        className={`${activityReveal.className} mx-auto max-w-[1440px] px-5 pb-24 sm:px-10 sm:pb-32`}
      >
        <SectionHead title="活动预告" en="Events" />
        <img
          src={activity.image}
          alt="活动预告海报"
          loading="lazy"
          decoding="async"
          className="mt-8 w-full object-cover"
        />
      </section>
    </>
  )
}
