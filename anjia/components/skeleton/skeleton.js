// 骨架屏。瀑布流的首屏必需品：图片高度要等真实尺寸回来才知道，在那之前如果
// 什么都不画，页面会先塌成空白再被内容顶开，抖得很难看。
//
// 只做透明度呼吸，不做扫光。扫光是一条高对比的亮带反复扫过整屏，在这套低对比
// 的暖色系里像个异物。
Component({
  properties: {
    // 图块的高宽比。默认几档错开，让骨架看起来就是参差的瀑布流而不是一排方块
    ratio: { type: Number, value: 1.25 },
    // 图块下面画几条文字线
    lines: { type: Number, value: 2 }
  },
  data: { lineList: [] },
  observers: {
    lines(n) {
      this.setData({ lineList: Array.from({ length: n }, (_, i) => i) })
    }
  },
  lifetimes: {
    attached() {
      this.setData({ lineList: Array.from({ length: this.data.lines }, (_, i) => i) })
    }
  }
})
