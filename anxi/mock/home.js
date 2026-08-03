// 首页内容。一期不接后端，先在这里写死；接口就绪后只需把导出换成请求结果。
// 图片是开业前的占位素材，实拍到位后直接替换 assets/home/ 下的同名文件即可。

const brandTitle = '杭州全案设计展厅'

// 顶部轮播：展厅现场，每张配一句现场注解
const heroSlides = [
  { image: '/assets/home/hero-01.jpg', note: '一进门的枯枝与木屏风' },
  { image: '/assets/home/hero-02.jpg', note: '会客厅，冬天真的生火' },
  { image: '/assets/home/hero-03.jpg', note: '一整面白墙，只放一炉' },
  { image: '/assets/home/hero-04.jpg', note: '花鸟壁纸，青绿的那面墙' },
  { image: '/assets/home/hero-05.jpg', note: '两扇窗之间的取暖角' }
]

// 展厅空间：序号是参观动线的顺序，不是编号装饰
const spaces = [
  {
    id: 'living',
    ordinal: '一',
    name: '会客厅',
    image: '/assets/home/space-01.jpg',
    note: '壁炉是通的，冬天点起来坐得住'
  },
  {
    id: 'atrium',
    ordinal: '二',
    name: '中庭',
    image: '/assets/home/space-02.jpg',
    note: '挑高六米，水晶灯从梁上垂下来'
  },
  {
    id: 'kitchen',
    ordinal: '三',
    name: '开放餐厨',
    image: '/assets/home/space-03.jpg',
    note: '岛台连着餐桌，做饭的人不被隔开'
  },
  {
    id: 'study',
    ordinal: '四',
    name: '书房',
    image: '/assets/home/space-04.jpg',
    note: '整面书墙，中间嵌一台燃木壁炉'
  },
  {
    id: 'bath',
    ordinal: '五',
    name: '主卫',
    image: '/assets/home/space-05.jpg',
    note: '独立浴缸，镜面里嵌着电视'
  },
  {
    id: 'fabric',
    ordinal: '六',
    name: '织物台',
    image: '/assets/home/space-06.jpg',
    note: '抱枕、窗帘、床品，都摊开让你上手摸'
  }
]

module.exports = { brandTitle, heroSlides, spaces }
