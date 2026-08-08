import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import './index.css'

// BrowserRouter 用真实路径而不是 hash：官网要给搜索引擎和外部链接用，
// /services 比 /#/services 干净。代价是服务器必须把未命中的路径回落到
// index.html，Caddy 那边已经配了 try_files（见 server/deploy/Caddyfile）
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
