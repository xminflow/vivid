// 安玺·集：商品详情。图集轮播 + 价格 + 简介 + 参数表 + 详情图。
//
// 下架的商品服务端回 404，这里直接把整页换成一句「已下架」，不做重试：
// 用户可能是从收藏、分享或者停在旧列表点进来的，对他来说「从来没有过」和
// 「刚下架」没有区别，都是这件买不了了。
//
// 底部操作栏：购物车入口、「加入购物车」、「立即购买」。后者不经过购物车，
// 订单的 source 记 direct——支付成功后不清车，否则会莫名其妙清掉用户车里的同款。
const api = require('../../utils/api.js')
const { yuan } = require('../../utils/money.js')
const homeMedia = require('../../utils/homeMedia.js')

Page({
  data: {
    id: '',
    product: null,
    price: '',
    // 轮播当前页，配合下面的小圆点
    imageIndex: 0,
    loading: true,
    // 非空表示这一页显示错误态而不是商品
    error: '',
    // 购物车角标显示的是件数，不是行数
    cartQuantity: 0,
    // 加购请求进行中，期间禁用按钮，避免连点加出好几件
    adding: false
  },

  onLoad(options) {
    const id = (options && options.id) || ''
    if (!id) {
      this.setData({ loading: false, error: '没有指定商品' })
      return
    }
    this.setData({ id })
    this.fetch(id)
  },

  fetch(id) {
    api
      .requestOptionalAuth({ url: `/api/shop/products/${id}` })
      .then(res => {
        if (res.statusCode === 404) {
          this.setData({ error: '这件商品已经下架了' })
          return
        }
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        const product = res.data.item
        this.setData({ product, price: yuan(product.priceCents) })
        wx.setNavigationBarTitle({ title: product.title })
      })
      .catch(err => {
        console.error('安玺·集商品详情加载失败', err)
        this.setData({ error: '加载失败，请稍后再试' })
      })
      .then(() => {
        this.setData({ loading: false })
      })
  },

  onShow() {
    // 从购物车页返回时车可能变了，角标要跟上
    this.refreshCart()
  },

  onImageChange(e) {
    this.setData({ imageIndex: e.detail.current })
  },

  /** 加入购物车。浏览不要登录，加购要——所以这里用 request 不是 requestOptionalAuth */
  onAdd() {
    if (this.data.adding || !this.data.product) return
    this.setData({ adding: true })
    api
      .request({
        url: '/api/shop/cart',
        method: 'POST',
        data: { productId: this.data.id, quantity: 1 }
      })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加入购物车失败')
        }
        wx.showToast({ title: '已加入购物车', icon: 'none' })
        this.refreshCart()
      })
      .catch(err => {
        console.error('加购失败', err)
        wx.showToast({ title: err.message || '加入购物车失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ adding: false })
      })
  },

  /** 立即购买：不加购，直接进结算页。
   *
   * 数量固定 1 —— 详情页没有数量选择器，要买多件走购物车。
   * 结算页会自己回查一次商品的当前价格与在售状态，这里不把商品数据传过去。
   */
  onBuy() {
    if (!this.data.product) return
    wx.navigateTo({
      url: `/pages/checkout/checkout?from=direct&productId=${this.data.id}&quantity=1`
    })
  },

  refreshCart() {
    return api
      .request({ url: '/api/shop/cart' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) return
        this.setData({ cartQuantity: res.data.quantity })
      })
      .catch(err => {
        // 角标拿不到就维持原样，不打扰用户——这不是他正在做的事
        console.error('购物车角标刷新失败', err)
      })
  },

  onCart() {
    wx.navigateTo({ url: '/pages/cart/cart' })
  },

  /** 点图看大图。图集和详情图各自成组预览，不混在一起 */
  onPreviewImage(e) {
    const urls = this.data.product ? this.data.product.images : []
    if (!urls.length) return
    wx.previewImage({ current: urls[e.currentTarget.dataset.i], urls })
  },

  onPreviewDetail(e) {
    const urls = this.data.product ? this.data.product.detailImages : []
    if (!urls.length) return
    wx.previewImage({ current: urls[e.currentTarget.dataset.i], urls })
  },

  onShareAppMessage() {
    const product = this.data.product
    return {
      title: product ? product.title : 'ANTONY CASA · 安玺·集',
      path: `/pages/product/product?id=${this.data.id}`,
      imageUrl: product && product.images.length ? product.images[0] : homeMedia.shareImage()
    }
  }
})
