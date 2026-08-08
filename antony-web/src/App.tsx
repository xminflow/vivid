import { Link, Route, Routes } from 'react-router-dom'
import { SiteLayout } from '@/layout/SiteLayout'
import { HomePage } from '@/features/home/HomePage'
import { ServicesPage } from '@/features/services/ServicesPage'
import { ApplyPage } from '@/features/services/ApplyPage'
import { BookingPage } from '@/features/booking/BookingPage'

function NotFound() {
  return (
    <div className="mx-auto max-w-[760px] px-5 py-32 text-center sm:px-10">
      <p className="text-[13px] tracking-[0.18em] text-grigio-chiaro">404</p>
      <h1 className="mt-3 text-[24px] tracking-display">页面不存在</h1>
      <Link
        to="/"
        className="mt-10 inline-block border border-nero px-10 py-3 text-[13px] tracking-meta transition-colors hover:bg-nero hover:text-bianco"
      >
        返回首页
      </Link>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      {/* 首页的顶栏要压在整屏实拍图上，其余页面是常规白底顶栏 */}
      <Route
        path="/"
        element={
          <SiteLayout transparentHeader>
            <HomePage />
          </SiteLayout>
        }
      />
      <Route
        path="/services"
        element={
          <SiteLayout>
            <ServicesPage />
          </SiteLayout>
        }
      />
      <Route
        path="/services/:serviceId"
        element={
          <SiteLayout>
            <ApplyPage />
          </SiteLayout>
        }
      />
      <Route
        path="/booking"
        element={
          <SiteLayout>
            <BookingPage />
          </SiteLayout>
        }
      />
      <Route
        path="*"
        element={
          <SiteLayout>
            <NotFound />
          </SiteLayout>
        }
      />
    </Routes>
  )
}
