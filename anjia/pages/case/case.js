// 案例详情。
//
// 作者那一行**不可点，也没有联系入口**：账号主页（需求文档 AJ-03）与站内私信都
// 还没做，造一个点进去发现是空的入口，比没有入口更伤。等那两块真做出来再让它活。

const api = require('../../utils/api.js')
const { views, date } = require('../../utils/format.js')

// 图区高度。按封面比例算，但压在一个上限内——竖构图的样板间常常是 3:4 甚至更长，
// 铺满就意味着第一屏只有一张图，标题和正文全被顶到折叠线以下
const SCREEN = 750
const MAX_H = 1000

Page({
  data: {
    loading: true,
    error: '',
    detail: null,
    swiperH: 900,
    current: 0
  },

  onLoad(query) {
    this.id = query.id || ''
    if (!this.id) {
      this.setData({ loading: false, error: '没有指定要看哪条案例' })
      return
    }
    this.load()
  },

  load() {
    this.setData({ loading: true, error: '' })
    api
      .readCase(this.id)
      .then(detail => {
        const cover = detail.cover || { w: 3, h: 4 }
        this.setData({
          loading: false,
          swiperH: Math.min(Math.round(SCREEN * (cover.h / cover.w)), MAX_H),
          detail: {
            ...detail,
            viewsText: views(detail.views),
            dateText: date(detail.publishedAt)
          }
        })
      })
      .catch(err => {
        console.error('[case] 加载详情失败', err)
        this.setData({ loading: false, error: err.message || '加载失败，请稍后再试' })
      })
  },

  onSwiper(e) {
    this.setData({ current: e.detail.current })
  },

  // 长按/点击看大图。图集是这一页的主体，不给放大等于让人隔着一层看
  onPreview(e) {
    const urls = this.data.detail.images.map(img => img.url).filter(Boolean)
    if (!urls.length) return
    wx.previewImage({ current: urls[e.currentTarget.dataset.index], urls })
  },

  onRetry() {
    this.load()
  }
})
