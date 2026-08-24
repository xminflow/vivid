// 头像。圆形，只负责「一张圆图 + 加载失败时的兜底」。
// 认证角标不在这里——它挂在名字旁边（见 author-row）。认证说明的是「这个账号是
// 哪家公司」，贴在名字上才读得通；贴在头像角上只是一枚看不懂的小点。
Component({
  properties: {
    src: { type: String, value: '' },
    // 单位 rpx。默认 40 是瀑布流作者行里的尺寸，大尺寸场景显式传
    size: { type: Number, value: 40 }
  },
  data: { failed: false },
  observers: {
    src() { this.setData({ failed: false }) }
  },
  methods: {
    onError() { this.setData({ failed: true }) }
  }
})
