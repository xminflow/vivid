// 首页内容。一期不接后端，先写死在这里；接口就绪后把导出换成请求结果即可。
// 图片是开业前的占位素材，实拍到位后直接替换 assets/home/ 下的同名文件。

const brand = {
  wordmark: 'ANTONY CASA',
  city: '杭州'
}

// 品牌陈述，压在首屏画廊上。主标语一行，下面一句铺垫
const about = {
  heading: '安东尼之家，帮你安好家',
  body: '提供一站式高端家居解决方案，从空间设计到实景落地全程把控'
}

// 顶部画廊：只放实拍，不压字
const heroSlides = [
  { image: '/assets/home/hero-01.jpg' },
  { image: '/assets/home/hero-02.jpg' },
  { image: '/assets/home/hero-03.jpg' },
  { image: '/assets/home/hero-04.jpg' },
  { image: '/assets/home/hero-05.jpg' }
]

// 展厅空间：序号是参观动线的顺序，不是编号装饰
const spaces = [
  {
    id: 'sala',
    ordinal: '01',
    name: '会客厅',
    en: 'Sala',
    floor: '一层',
    hours: '周二至周日 10:00 - 19:00',
    image: '/assets/home/space-01.jpg'
  },
  {
    id: 'atrio',
    ordinal: '02',
    name: '中庭',
    en: 'Atrio',
    floor: '一层',
    hours: '周二至周日 10:00 - 19:00',
    image: '/assets/home/space-02.jpg'
  },
  {
    id: 'cucina',
    ordinal: '03',
    name: '开放餐厨',
    en: 'Cucina',
    floor: '一层',
    hours: '周二至周日 10:00 - 19:00',
    image: '/assets/home/space-03.jpg'
  },
  {
    id: 'studio',
    ordinal: '04',
    name: '书房',
    en: 'Studio',
    floor: '二层',
    hours: '周二至周日 10:00 - 18:00',
    image: '/assets/home/space-04.jpg'
  },
  {
    id: 'bagno',
    ordinal: '05',
    name: '主卫',
    en: 'Bagno',
    floor: '二层',
    hours: '周二至周日 10:00 - 18:00',
    image: '/assets/home/space-05.jpg'
  },
  {
    id: 'tessuti',
    ordinal: '06',
    name: '织物台',
    en: 'Tessuti',
    floor: '二层',
    hours: '周二至周日 10:00 - 18:00',
    image: '/assets/home/space-06.jpg'
  }
]

// 活动预告：整张海报直接铺出来，文案都在图里，页面不再另起一套字
const activity = {
  image: '/assets/home/activity.jpg'
}

module.exports = { brand, about, heroSlides, spaces, activity }
