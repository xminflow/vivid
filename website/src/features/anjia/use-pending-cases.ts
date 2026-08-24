import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { countPendingCases } from './api'

/**
 * 左侧导航上「案例」那一条的待审数量。
 *
 * 为什么值得单独跑一次请求：案例的量级会比企业认证大一到两个数量级，而运营大部分
 * 时间待在别的页面上。没有这个数字，他就得靠猜要不要点进案例页看一眼——认证那一条
 * 没有角标是因为它一天也就几条，靠不靠角标都一样。
 *
 * 跟着路由变化重取：这一页自己的分页数字是随列表下发的、永远是新的，导航上这个只
 * 需要回答「我不在那一页时，有没有新的在等我」。切页时刷新正好覆盖这个场景，
 * 不值得为它起一个轮询。
 *
 * 拿的是列表响应里的 counts（见 countPendingCases）：那个数字随列表一起下发，
 * 不必为它单开一个接口。
 */
export function usePendingCases(): number {
  const [pending, setPending] = useState(0)
  const location = useLocation()

  useEffect(() => {
    let alive = true
    countPendingCases()
      .then((body) => {
        if (alive) setPending(body.counts.pending)
      })
      // 静默失败：这是个角标，为它弹一个错误提示是本末倒置。真连不上后台，
      // 用户点开任何一页都会看到那一页自己的报错
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [location.pathname])

  return pending
}
