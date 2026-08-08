import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useSession } from './session-context'

/** 没登录一律送到登录页，并把原地址记下来，登录完直接回去。 */
export function RequireAuth() {
  const { user, ready } = useSession()
  const location = useLocation()

  // 首次 /auth/me 还没回来时什么都不渲染：这时候判断会先闪一下登录页，
  // 已经登录的人每次刷新都看见闪一下，像是登录态不稳
  if (!ready) return null

  // 带上 search：像 /appointments?page=3&status=pending 这种，只记 pathname 的话
  // 登录完回去的是第一页，筛选条件全丢了
  if (!user)
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />

  return <Outlet />
}
