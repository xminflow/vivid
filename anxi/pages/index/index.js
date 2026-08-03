// 首页：上 2/3 屏展厅轮播，下方按参观动线排的空间预约轮播
const { brandTitle, heroSlides, spaces } = require('../../mock/home.js')

Page({
  data: {
    titleChars: brandTitle.split(''),
    heroSlides,
    spaces,
    heroIndex: 0,
    spaceIndex: 0,
    heroHeight: 0,
    statusBarHeight: 20,
    current: '01',
    total: '00'
  },

  onLoad() {
    // getSystemInfoSync 已废弃，用 getWindowInfo；老基础库上兜底回去
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    this.setData({
      heroHeight: Math.round(info.screenHeight * 2 / 3),
      statusBarHeight: info.statusBarHeight || 20,
      total: pad(heroSlides.length)
    })
  },

  onHeroChange(e) {
    this.setData({
      heroIndex: e.detail.current,
      current: pad(e.detail.current + 1)
    })
  },

  onSpaceChange(e) {
    this.setData({ spaceIndex: e.detail.current })
  },

  onBook(e) {
    const { name } = e.currentTarget.dataset
    // TODO: QA-01 预约表单页就位后改成 wx.navigateTo('/pages/booking/booking?space=' + id)
    wx.showToast({
      title: name + '的预约通道即将开放',
      icon: 'none',
      duration: 2000
    })
  }
})

function pad(n) {
  return n < 10 ? '0' + n : String(n)
}
