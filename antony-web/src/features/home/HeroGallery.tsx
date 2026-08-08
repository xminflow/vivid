/**
 * 首屏画廊。两套版式，因为实拍是横构图（4:3）：
 *
 *   手机  图按原比例完整铺开，品牌陈述落在图下方的深色区块。
 *         竖屏里再让横图撑满高度的话，两侧会被裁掉六成以上，只剩中间一条——
 *         这是用户反馈「看不完整」的原因。
 *   桌面  视口本来就是横的，图撑满整屏、文字压在图上，不存在裁剪问题。
 *
 * 切换用 opacity 交叉淡入而不是横向位移：位移在宽屏上会露出相邻图的边缘，
 * 淡入在任何宽高比下都成立。
 */
import { useEffect, useState } from 'react'
import { about, brand, heroSlides } from './content'

const INTERVAL = 5000

export function HeroGallery() {
  const [index, setIndex] = useState(0)
  // wordmark 落定动画：进场时字距宽，随后收紧，和小程序 settled 的效果一致
  const [settled, setSettled] = useState(false)

  useEffect(() => {
    const t = window.setTimeout(() => setSettled(true), 120)
    return () => window.clearTimeout(t)
  }, [])

  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) return

    const timer = window.setInterval(() => {
      setIndex((i) => (i + 1) % heroSlides.length)
    }, INTERVAL)
    return () => window.clearInterval(timer)
  }, [])

  return (
    // 手机是「图 + 文」上下两段的普通流，高度顺着内容走——强撑满屏的话，
    // 图按原比例只占上面一小半，中间会空出一大片黑。桌面切回 relative + 满屏，
    // 文字绝对定位压在图上
    <section className="flex w-full flex-col bg-nero sm:relative sm:block sm:h-dvh sm:overflow-hidden">
      {/* 图区。手机用 aspect 锁住原始比例，保证整张图都在；桌面铺满整个 section */}
      <div className="relative aspect-[4/3] w-full shrink-0 sm:absolute sm:inset-0 sm:aspect-auto sm:h-full">
        {heroSlides.map((src, i) => (
          <img
            key={src}
            src={src}
            alt=""
            // 首图要最快出来，其余交给浏览器排队；解码也异步，避免大图阻塞首屏
            loading={i === 0 ? 'eager' : 'lazy'}
            decoding="async"
            fetchPriority={i === 0 ? 'high' : 'low'}
            // 手机用 contain：容器已经是图的比例，contain 能担保一个像素都不裁；
            // 桌面用 cover 撑满不留边
            className={`absolute inset-0 h-full w-full object-contain transition-opacity duration-[1400ms] ease-out sm:object-cover ${
              i === index ? 'opacity-100' : 'opacity-0'
            }`}
          />
        ))}

        {/* 顶部渐变两端都要：顶栏是透明的、压在图上，遇到浅色天花板这类亮部
            wordmark 会糊掉。底部渐变只有桌面需要——手机上文字在图外面的黑底上，不用压 */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-gradient-to-b from-nero/60 to-transparent sm:h-44" />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 hidden h-2/3 bg-gradient-to-t from-nero/90 via-nero/45 to-transparent sm:block" />
      </div>

      {/* 文案区。手机上紧接在图下方；桌面绝对定位压回图上 */}
      <div className="px-5 pt-9 pb-14 sm:absolute sm:inset-x-0 sm:bottom-0 sm:px-10 sm:pt-0 sm:pb-20">
        <div className="mx-auto w-full max-w-[1440px]">
          {/* 限宽用 em 不用 ch：ch 是数字 0 的宽度，中文一个字约合 2ch，
              按 ch 设上限会让中文标题在宽屏上莫名其妙地折行。1em = 一个中文字宽 */}
          <h1
            className={`text-on-photo max-w-[15em] text-[28px] leading-tight text-su-foto transition-[letter-spacing] duration-[800ms] sm:text-[44px] lg:text-[56px] ${
              settled ? 'tracking-display' : 'tracking-[0.12em]'
            }`}
          >
            {about.heading}
          </h1>
          <p className="text-on-photo mt-4 max-w-[34em] text-[13px] leading-loose tracking-meta text-su-foto-debole sm:text-[15px]">
            {about.body}
          </p>
          <p className="mt-6 text-[12px] tracking-[0.16em] text-su-foto-debole">
            {brand.wordmark} · {brand.city}
          </p>

          <div className="mt-8 flex gap-2" role="tablist" aria-label="首屏图片">
            {heroSlides.map((src, i) => (
              <button
                key={src}
                role="tab"
                aria-selected={i === index}
                aria-label={`第 ${i + 1} 张`}
                onClick={() => setIndex(i)}
                className={`h-[2px] transition-all duration-300 ${
                  i === index ? 'w-10 bg-su-foto' : 'w-5 bg-su-foto/40'
                }`}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
