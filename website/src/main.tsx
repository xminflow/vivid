import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { Toaster } from '@/components/ui/sonner'
import { AppShell } from '@/app-shell'
import { LoginPage } from '@/features/auth/login-page'
import { RequireAuth } from '@/features/auth/require-auth'
import { SessionProvider } from '@/features/auth/session-context'
import { AppointmentsPage } from '@/features/antony/appointments-page'
import { HomeMediaPage } from '@/features/antony/home-media-page'
import { ServiceApplicationsPage } from '@/features/antony/service-applications-page'
import { AccountsPage } from '@/features/accounts/accounts-page'

import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          {/* 除登录页外一律要登录。守卫套在 AppShell 外面，
              未登录时连侧栏都不该渲染出来 */}
          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/antony/appointments" replace />} />
              <Route path="/antony/appointments" element={<AppointmentsPage />} />
              <Route path="/antony/service-applications" element={<ServiceApplicationsPage />} />
              <Route path="/antony/home-media" element={<HomeMediaPage />} />
              {/* 只有超管进得去，普通管理员访问会吃服务端的 403 并在页面上报错 */}
              <Route path="/accounts" element={<AccountsPage />} />
              {/* 打错地址不该白屏，回到默认页 */}
              <Route path="*" element={<Navigate to="/antony/appointments" replace />} />
            </Route>
          </Route>
        </Routes>
      </SessionProvider>
    </BrowserRouter>
    {/* 接口报错统一走 toast，见 features/antony/use-paged-list.ts */}
    <Toaster position="top-center" richColors />
  </StrictMode>,
)
