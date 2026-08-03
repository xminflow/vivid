// 首页：实拍画廊 + 品牌陈述 + 可预约的六个空间。
const { brand, about, heroSlides, spaces, activity } = require('../../mock/home.js')

// getWindowInfo 是新基础库的接口，低版本回落到 getSystemInfoSync
function readWindow() {
  if (typeof wx.getWindowInfo === 'function') {
    return wx.getWindowInfo()
  }
  return wx.getSystemInfoSync()
}

Page({
  data: {
    brand,
    about,
    heroSlides,
    spaces,
    activity,
    heroIndex: 0,
    spaceIndex: 0,
    statusBarHeight: 20,
    heroHeight: 480,
    settled: false // 入场：字距从松收到位，只跑一次
  },

  onLoad() {
    const info = readWindow()
    const windowHeight = info.windowHeight || 640

    this.setData({
      statusBarHeight: info.statusBarHeight || 20,
      // 画廊占三分之二强：品牌陈述放得下，又不至于占满一屏；余下的高度把「展厅预约」露出来
      heroHeight: Math.round(windowHeight * 0.68)
    })

    setTimeout(() => this.setData({ settled: true }), 100)
  },

  onHeroChange(e) {
    this.setData({ heroIndex: e.detail.current })
  },

  onSpaceChange(e) {
    this.setData({ spaceIndex: e.detail.current })
  },

  onBook(e) {
    const { id, name } = e.currentTarget.dataset
    wx.navigateTo({
      url: `/pages/booking/booking?spaceId=${id}&spaceName=${encodeURIComponent(name)}`
    })
  },

  onShareAppMessage() {
    return {
      title: 'ANTONY CASA 杭州展厅 · 六个空间',
      path: '/pages/index/index'
    }
  }
})
