// 双列瀑布流的版面计算。
//
// 抽成独立模块有两个理由：一是 rpx 的算术只能在 JS 里做（WXSS 拿不到列高），
// 页面逻辑里塞满常数会看不清；二是设计评审用的浏览器预览要跑**同一份**算法，
// 两份实现迟早会走样，那时候预览就不再能代表小程序。
//
// ⚠️ SCREEN / GUTTER / COL_GAP 与 styles/tokens.wxss 里的 --gutter / --col-gap
// 是同一组数。改了那边，这里必须跟着改。

const SCREEN = 750
const GUTTER = 24
const COL_GAP = 16

const COL_W = (SCREEN - GUTTER * 2 - COL_GAP) / 2   // 343rpx

// 卡片正文的高度构成，用来估算列高。估不准只会让两列略微不齐，不会出错
const PAD = 16
const TITLE_LINE = 39        // fs-card 26rpx × 行高 1.5
const TITLE_MAX_LINES = 2
const AUTHOR_H = 40          // 与头像直径一致
const AUTHOR_GAP = 16
const CHARS_PER_LINE = Math.floor((COL_W - PAD * 2) / 26)

// 图片高度必须在图片加载**之前**就算出来，否则页面会先塌成空白再被内容顶开。
// 这也是为什么每条数据都要带 w / h——真实接口同样要下发，不是 mock 的特殊照顾
function measure(item) {
  const imgH = Math.round(COL_W * (item.h / item.w))
  const lines = Math.min(Math.ceil(item.title.length / CHARS_PER_LINE), TITLE_MAX_LINES)
  const bodyH = PAD * 2 + lines * TITLE_LINE + AUTHOR_GAP + AUTHOR_H
  return { imgH, cardH: imgH + bodyH }
}

// 贪心装箱：每张卡进当前较矮的那一列。瀑布流不需要最优解，只要两列不明显失衡
function pack(list) {
  const cols = [[], []]
  const heights = [0, 0]
  list.forEach((item) => {
    const { imgH, cardH } = measure(item)
    const i = heights[0] <= heights[1] ? 0 : 1
    cols[i].push({ ...item, imgH })
    heights[i] += cardH + COL_GAP
  })
  return cols
}

module.exports = { COL_W, measure, pack }
