/**
 * 首页内容。与小程序 mock/home.js 是同一份文案、同一批图，改动要两边同步。
 * 图片走 COS，换图只要覆盖同名对象，两端都不用发版/重新构建。
 */
import { STATIC_BASE } from '@/shared/config'

export const brand = {
  wordmark: 'ANTONY CASA',
  city: '杭州',
} as const

/** 品牌陈述，压在首屏画廊上。主标语一行，下面一句铺垫 */
export const about = {
  heading: '安东尼之家，帮你安好家',
  body: '提供一站式高端家居解决方案，从空间设计到实景落地全程把控',
} as const

/** 顶部画廊：只放实拍，不压字 */
export const heroSlides: readonly string[] = [
  `${STATIC_BASE}/home/hero-01.jpg`,
  `${STATIC_BASE}/home/hero-02.jpg`,
  `${STATIC_BASE}/home/hero-03.jpg`,
  `${STATIC_BASE}/home/hero-04.jpg`,
  `${STATIC_BASE}/home/hero-05.jpg`,
]

/** 展厅只有一个，六张实拍轮着看。images 的顺序就是参观动线的顺序 */
export const showroom = {
  name: '杭州展厅',
  en: 'Showroom',
  floor: '一至二层',
  hours: '周二至周日 10:00 - 19:00',
  images: [
    `${STATIC_BASE}/home/space-01.jpg`,
    `${STATIC_BASE}/home/space-02.jpg`,
    `${STATIC_BASE}/home/space-03.jpg`,
    `${STATIC_BASE}/home/space-04.jpg`,
    `${STATIC_BASE}/home/space-05.jpg`,
    `${STATIC_BASE}/home/space-06.jpg`,
  ],
} as const

/** 活动预告：整张海报直接铺出来，文案都在图里，页面不再另起一套字 */
export const activity = {
  image: `${STATIC_BASE}/home/activity.jpg`,
} as const
