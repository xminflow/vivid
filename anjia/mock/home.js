// 首页内容流的假数据。
//
// ⚠️ 这是**样板页专用**的占位数据，不是兜底值：真实的案例走接口下发（首页内容流
// 还没开发）。这个文件的唯一职责是把样式系统在真实参差比例下跑一遍，验收完成
// 后连同 assets/mock/ 一起删掉。
//
// 每条都带 w / h：瀑布流必须在图片加载**之前**就知道它多高，否则页面会先塌成
// 空白再被内容顶开。真实接口同样要下发宽高，这不是 mock 的特殊照顾。

const IMG = '/assets/mock'

// 全部作者都是企业认证账号——首页内容流本来就只有企业认证账号能发布（见
// CONTEXT.md「案例」）。正因为如此，**卡片上不画认证角标**：一屏十二张卡全带
// 同一枚角标，它就不再传递任何信息，只是十二处重复的噪点。角标要留给随安而遇
// 与安选有品，那里个人与企业混发，它才真的在区分两种人。
const authors = {
  yijing:  { name: '一境设计',   avatar: `${IMG}/av-1571460.jpg` },
  yunlu:   { name: '云庐置业',   avatar: `${IMG}/av-1080721.jpg` },
  muye:    { name: '木也家具',   avatar: `${IMG}/av-276724.jpg` },
  shiguang:{ name: '拾光设计',   avatar: `${IMG}/av-1350789.jpg` },
  baihe:   { name: '白盒空间',   avatar: `${IMG}/av-2062431.jpg` },
  yujian:  { name: '屿见地产',   avatar: `${IMG}/av-1643383.jpg` },
  miluo:   { name: '米洛设计',   avatar: `${IMG}/av-6301182.jpg` },
  guanfu:  { name: '观复空间',   avatar: `${IMG}/av-3935350.jpg` }
}

const cases = [
  { id: '1',  title: '杭州 140㎡ 现代东方，客厅去掉主灯之后',  image: `${IMG}/33685863.jpg`, w: 600, h: 900,  views: 3421,  author: authors.yijing },
  { id: '2',  title: '样板间实拍｜滨江 · 云庐三居',            image: `${IMG}/14613821.jpg`, w: 600, h: 337,  views: 8752,  author: authors.yunlu },
  { id: '3',  title: '新品｜胡桃木餐边柜，做了三版才敢发',      image: `${IMG}/17947888.jpg`, w: 600, h: 800,  views: 1204,  author: authors.muye },
  { id: '4',  title: '老破小改造：52㎡ 装下一家三口和一只猫',   image: `${IMG}/35482185.jpg`, w: 600, h: 1067, views: 25600, author: authors.shiguang },
  { id: '5',  title: '极简白 + 微水泥，业主说像美术馆',        image: `${IMG}/31737841.jpg`, w: 600, h: 400,  views: 6180,  author: authors.baihe },
  { id: '6',  title: '软装清单公开｜这套房的窗帘选了六次',      image: `${IMG}/34549311.jpg`, w: 600, h: 900,  views: 940,   author: authors.yijing },
  { id: '7',  title: '交付实拍｜西湖区 · 屿见',                image: `${IMG}/34117279.jpg`, w: 600, h: 800,  views: 12030, author: authors.yujian },
  { id: '8',  title: '意式极简的边界在哪，聊聊我们踩过的坑',    image: `${IMG}/35189707.jpg`, w: 600, h: 428,  views: 3380,  author: authors.miluo },
  { id: '9',  title: '新中式不等于红木',                       image: `${IMG}/38845158.jpg`, w: 600, h: 900,  views: 5610,  author: authors.guanfu },
  { id: '10', title: '厨房动线改了四遍，最后长这样',            image: `${IMG}/38310837.jpg`, w: 600, h: 832,  views: 2170,  author: authors.shiguang },
  { id: '11', title: '品牌上新｜2026 春夏软装配色手册',         image: `${IMG}/12379606.jpg`, w: 600, h: 1067, views: 780,   author: authors.muye },
  { id: '12', title: '大平层 260㎡ 全屋定制交付纪实',           image: `${IMG}/37859439.jpg`, w: 600, h: 900,  views: 15400, author: authors.guanfu }
]

module.exports = { cases }
