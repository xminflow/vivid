/**
 * 站点外壳：顶栏 + 内容 + 页脚。
 *
 * 顶栏在首页要压在整屏实拍图上（透明、白字），滚过首屏后变成白底黑字；
 * 其它页面没有首屏大图，直接就是白底。这个差别由 transparent 参数控制，
 * 页面自己声明，而不是在这里判断路由——将来加页面不用回头改这里。
 */
import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { brand } from '@/features/home/content'
import { advisor } from '@/shared/contact'

const NAV = [
  { to: '/', label: '首页' },
  { to: '/services', label: '服务' },
  { to: '/booking', label: '预约参观' },
] as const

function SiteHeader({ transparent }: { transparent: boolean }) {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    if (!transparent) return
    const onScroll = () => setScrolled(window.scrollY > window.innerHeight * 0.72)
    onScroll()
    // passive：这个回调不会 preventDefault，声明后浏览器不必等它就能滚
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [transparent])

  const solid = !transparent || scrolled

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-500 ${
        solid ? 'bg-sfondo/95 text-nero backdrop-blur' : 'text-su-foto'
      }`}
    >
      <div className="mx-auto flex max-w-[1440px] items-center justify-between px-5 py-4 sm:px-10">
        <Link
          to="/"
          className="text-[15px] tracking-[0.16em] transition-opacity hover:opacity-70 sm:text-[17px]"
        >
          {brand.wordmark}
        </Link>

        <nav className="flex items-center gap-5 text-[13px] tracking-meta sm:gap-8">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `transition-opacity hover:opacity-70 ${isActive ? 'opacity-100' : 'opacity-65'}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  )
}

function SiteFooter() {
  return (
    <footer className="border-t border-hairline bg-bianco">
      <div className="mx-auto grid max-w-[1440px] gap-10 px-5 py-14 sm:grid-cols-2 sm:px-10 lg:grid-cols-3">
        <div>
          <p className="text-[16px] tracking-[0.16em]">{brand.wordmark}</p>
          <p className="mt-3 text-[13px] leading-loose text-grigio">
            {brand.city}展厅
            <br />
            周二至周日 10:00 - 19:00
          </p>
        </div>

        <div className="text-[13px] leading-loose text-grigio">
          <p className="text-nero">联系我们</p>
          {/* tel: 在手机上点了直接拨号，桌面端交给系统处理 */}
          <a href={`tel:${advisor.phone}`} className="mt-3 block transition-opacity hover:opacity-70">
            {advisor.phone}
          </a>
          <Link to="/booking" className="mt-1 block transition-opacity hover:opacity-70">
            预约到店参观 ›
          </Link>
          <Link to="/services" className="mt-1 block transition-opacity hover:opacity-70">
            申请服务 ›
          </Link>
        </div>

        <div className="text-[13px] text-grigio">
          <p className="text-nero">添加专属顾问</p>
          <img
            src="/wechat-qr.png"
            alt="专属顾问微信二维码"
            className="mt-3 h-28 w-28 object-contain"
            loading="lazy"
          />
        </div>
      </div>

      <div className="border-t border-hairline">
        <p className="mx-auto max-w-[1440px] px-5 py-5 text-[12px] text-grigio-chiaro sm:px-10">
          © {new Date().getFullYear()} {brand.wordmark}
        </p>
      </div>
    </footer>
  )
}

export function SiteLayout({
  children,
  transparentHeader = false,
}: {
  children: ReactNode
  transparentHeader?: boolean
}) {
  const { pathname } = useLocation()

  // 路由切换后浏览器会保留上一页的滚动位置，从服务页点进申请页会停在半路
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])

  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader transparent={transparentHeader} />
      {/* 顶栏是 fixed 的，不透明时要给内容让出高度，否则第一屏会被盖住 */}
      <main className={`flex-1 ${transparentHeader ? '' : 'pt-[60px]'}`}>{children}</main>
      <SiteFooter />
    </div>
  )
}
