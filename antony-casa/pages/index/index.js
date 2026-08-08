// 首页：实拍画廊 + 品牌陈述 + 展厅预约。
//
// 文案（品牌标、陈述、展厅名称营业时间）仍写在 mock/home.js；三组图改由后台配，
// 从 GET /api/home 拉，来源和兜底规则见 utils/homeMedia.js。
const { brand, about, heroSlides, showroom, activity, shareImage } = require('../../mock/home.js')
const homeMedia = require('../../utils/homeMedia.js')

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
    showroom,
    activity,
    heroIndex: 0,
    photoIndex: 0,
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

    // 先用本机缓存（没有就是包内默认值）把图铺上，首屏不等网络；
    // 再去拉最新配置覆盖。两步都走同一个 applyMedia
    this.applyMedia(homeMedia.local())
    homeMedia
      .refresh()
      .then(media => this.applyMedia(media))
      .catch(err => {
        // 拉不到就继续用上面那份，但必须留下痕迹：不打日志的话，
        // 「后台改了图小程序没变」这种问题在真机上完全无从查起
        console.error('[index] 首页配图刷新失败，继续用缓存或默认值', err)
      })

    setTimeout(() => this.setData({ settled: true }), 100)
  },

  /**
   * 把一份配图数据铺到页面上。
   * media 的键就是 setData 的路径，见 utils/homeMedia.js 的 toPageData。
   */
  applyMedia(media) {
    const patch = {
      heroSlides: media.heroSlides,
      'showroom.images': media['showroom.images'],
      'activity.image': media['activity.image']
    }

    // 刷新后图可能变少：轮播停在第 5 张、新配置只有 2 张的话，页码指示器会一个都不亮。
    // 越界就回到第一张，指示器和图始终对得上
    if (this.data.heroIndex >= patch.heroSlides.length) patch.heroIndex = 0
    if (this.data.photoIndex >= patch['showroom.images'].length) patch.photoIndex = 0

    this.setData(patch)
  },

  onHeroChange(e) {
    this.setData({ heroIndex: e.detail.current })
  },

  onPhotoChange(e) {
    this.setData({ photoIndex: e.detail.current })
  },

  onBook() {
    wx.navigateTo({ url: '/pages/booking/booking' })
  },

  onShareAppMessage() {
    return {
      title: 'ANTONY CASA 杭州展厅',
      path: '/pages/index/index',
      imageUrl: shareImage
    }
  }
})
