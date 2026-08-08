import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ChangePasswordDialog } from '@/features/auth/change-password-dialog'
import { useSession } from '@/features/auth/session-context'
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

// 只有超管看得见。服务端也拦（见 admin_accounts.py），
// 这里隐藏只是不给普通管理员看见一个点不动的入口
const SUPER_NAV = {
  group: '系统',
  items: [{ to: '/accounts', label: '账号管理' }],
}

export function AppShell() {
  const { user, signOut } = useSession()
  const [changing, setChanging] = useState(false)
  const sections = user?.isSuper ? [...NAV, SUPER_NAV] : NAV

  // signOut() 内部是 try/finally 没有 catch：本地 token 和 user 一定会清干净，
  // 但服务端 logout 请求失败时异常会继续往上抛。本地状态已经退出、页面马上要跳
  // 登录页了，这个失败只是「不太重要的收尾没做成」，不用打断用户，提示一下就够。
  const handleSignOut = () => {
    signOut().catch(() => {
      toast.error('退出登录时通知服务端失败，但本地登录状态已清除')
    })
  }

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r border-border bg-card">
        <div className="flex h-14 items-center border-b border-border px-4 text-sm font-semibold tracking-wide">
          小程序矩阵 · 管理后台
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {sections.map((section) => (
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

        <div className="border-t border-border p-3">
          <div className="truncate text-sm font-medium" title={user?.username}>
            {user?.displayName || user?.username}
          </div>
          <div className="mb-2 text-xs text-muted-foreground">
            {user?.isSuper ? '超级管理员' : '管理员'}
          </div>
          <div className="flex gap-1">
            <Button variant="outline" size="sm" onClick={() => setChanging(true)}>
              修改密码
            </Button>
            <Button variant="ghost" size="sm" onClick={handleSignOut}>
              退出登录
            </Button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <Outlet />
      </main>

      <ChangePasswordDialog open={changing} onOpenChange={setChanging} />
    </div>
  )
}
