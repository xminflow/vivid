import { NavLink, Outlet } from 'react-router-dom'

import { cn } from '@/lib/utils'

// 一个后台管一个矩阵：左侧按小程序分组，组内是各自的数据模块。
// 现在只有安东尼之家；再接别的小程序时在这里加一组，功能代码各自留在 features/ 下。
const NAV = [
  {
    group: '安东尼之家',
    items: [
      { to: '/antony/appointments', label: '展厅预约申请' },
      { to: '/antony/service-applications', label: '服务申请' },
      { to: '/antony/home-media', label: '首页图片' },
    ],
  },
]

export function AppShell() {
  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <aside className="sticky top-0 h-screen w-52 shrink-0 border-r border-border bg-card">
        <div className="flex h-14 items-center border-b border-border px-4 text-sm font-semibold tracking-wide">
          小程序矩阵 · 管理后台
        </div>
        <nav className="p-2">
          {NAV.map((section) => (
            <div key={section.group} className="mb-3">
              <div className="px-2 py-1 text-xs text-muted-foreground">{section.group}</div>
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      'block rounded-md px-2 py-1.5 text-sm transition-colors',
                      isActive
                        ? 'bg-muted font-medium text-foreground'
                        : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
