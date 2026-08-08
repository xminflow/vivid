import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { Toaster } from '@/components/ui/sonner'
import { AppShell } from '@/app-shell'
import { AppointmentsPage } from '@/features/antony/appointments-page'
import { HomeMediaPage } from '@/features/antony/home-media-page'
import { ServiceApplicationsPage } from '@/features/antony/service-applications-page'

import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/antony/appointments" replace />} />
          <Route path="/antony/appointments" element={<AppointmentsPage />} />
          <Route path="/antony/service-applications" element={<ServiceApplicationsPage />} />
          <Route path="/antony/home-media" element={<HomeMediaPage />} />
          {/* 打错地址不该白屏，回到默认页 */}
          <Route path="*" element={<Navigate to="/antony/appointments" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
    {/* 接口报错统一走 toast，见 features/antony/use-paged-list.ts */}
    <Toaster position="top-center" richColors />
  </StrictMode>,
)
