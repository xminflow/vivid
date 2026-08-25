// 首页内容。
//
// ⚠️ 三组图（heroSlides / showroom.images / activity.image）现在是**兜底值**，
// 不是实际展示的内容：正常情况下走后台配置（GET /api/home），只有从没成功拉到过
// 配置、或后台把某一组清空了，才会用到这里的值。改图请去后台「首页图片」页，
// 改这里只影响兜底。合并规则见 utils/homeMedia.js。
//
// 文案（brand / about / showroom 的名称营业时间）仍然只有这一份，后台不管，
// 改了要发版。
//
// 图存在 COS 上不进包（见 utils/config.js 的 STATIC_BASE）。换兜底图就换掉
// assets/home/ 下的同名文件，跑一次 server/scripts/upload_static.py 覆盖上去
// 即可生效，小程序不用发版。
const { STATIC_BASE } = require('../utils/config.js')

const brand = {
  wordmark: 'ANTONY CASA',
  city: '杭州'
}

// 品牌陈述，压在首屏画廊上。主标语一行，下面一句铺垫
const about = {
  heading: '安东尼之家，帮你安好家',
  body: '提供一站式高端家居解决方案，从空间设计到实景落地全程把控'
}

// 顶部画廊：只放实拍，不压字。
// 第一张同时是各页转发卡片的兜底配图（后台没单独配「小程序封面」时用它，见
// utils/homeMedia.js 的 shareImage），所以它还要经得住微信 5:4 居中裁剪、
// 最短边不小于 300px——hero-01 是 1920x1440，够用
const heroSlides = [
  { image: `${STATIC_BASE}/home/hero-01.jpg` },
  { image: `${STATIC_BASE}/home/hero-02.jpg` },
  { image: `${STATIC_BASE}/home/hero-03.jpg` },
  { image: `${STATIC_BASE}/home/hero-04.jpg` },
  { image: `${STATIC_BASE}/home/hero-05.jpg` }
]

// 展厅只有一个，六张实拍轮着看。images 的顺序就是参观动线的顺序
const showroom = {
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
    `${STATIC_BASE}/home/space-06.jpg`
  ]
}

// 活动预告：整张海报直接铺出来，文案都在图里，页面不再另起一套字
const activity = {
  image: `${STATIC_BASE}/home/activity.jpg`
}

module.exports = { brand, about, heroSlides, showroom, activity }
