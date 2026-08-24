// 安玺·集：左侧分类栏 + 右侧商品列表，右下角悬浮购物车入口。
//
// 只出在售商品——服务端已经按 status = 'active' 过滤，这里不再做二次判断。
// 「在售」既是能否被购买的判据，也是能否被看到的判据，不存在中间态。
//
// 两栏各自独立滚动，所以分页**不能**用 onReachBottom（页面本身不滚），
// 走右栏 scroll-view 的 bindscrolltolower；下拉刷新同理，用 scroll-view 的
// refresher 而不是页面的 enablePullDownRefresh。
//
// 浏览走 requestOptionalAuth（登录失败也能看），加购走 request（车按人存，必须登录）。
const api = require('../../utils/api.js')
const { yuan } = require('../../utils/money.js')
const homeMedia = require('../../utils/homeMedia.js')

const PAGE_SIZE = 20

Page({
  data: {
    categories: [],
    // '' 表示不筛分类，也就是「全部」
    categoryId: '',
    keyword: '',
    items: [],
    page: 1,
    total: 0,
    loading: false,
    // 首屏加载完之前不显示空态，否则会闪一下「没有商品」
    loaded: false,
    // 下拉刷新的转圈由我们自己控制，scroll-view 不会自动收
    refreshing: false,
    // 购物车角标显示的是**件数**，不是行数
    cartQuantity: 0
  },

  onLoad() {
    this.loadCategories()
    this.reload()
  },

  onShow() {
    // 从购物车页返回时车可能变了，角标要跟上
    this.refreshCart()
  },

  loadCategories() {
    return api
      .requestOptionalAuth({ url: '/api/shop/categories' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) return
        this.setData({ categories: res.data.items })
      })
      .catch(err => {
        // 分类拉不到不该挡住商品列表：左栏空着，右栏照样出全部商品
        console.error('安玺·集分类加载失败', err)
      })
  },

  /** 回到第一页重查。切分类、点搜索、下拉刷新都走这里 */
  reload() {
    return this.fetch(1, true)
  },

  fetch(page, replace) {
    this.setData({ loading: true })
    return api
      .requestOptionalAuth({
        url: '/api/shop/products',
        data: {
          page,
          pageSize: PAGE_SIZE,
          keyword: this.data.keyword,
          categoryId: this.data.categoryId
        }
      })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        // 价格在这里就换算好：wxml 里没法调函数，塞进 data 才能直接渲染
        const rows = res.data.items.map(item => ({ ...item, price: yuan(item.priceCents) }))
        this.setData({
          items: replace ? rows : this.data.items.concat(rows),
          total: res.data.total,
          page
        })
      })
      .catch(err => {
        console.error('安玺·集商品加载失败', err)
        wx.showToast({ title: '加载失败，请稍后再试', icon: 'none' })
      })
      .then(() => {
        this.setData({ loading: false, loaded: true, refreshing: false })
      })
  },

  onScrollToLower() {
    if (this.data.loading) return
    if (this.data.items.length >= this.data.total) return
    this.fetch(this.data.page + 1, false)
  },

  onRefresh() {
    this.setData({ refreshing: true })
    this.reload()
  },

  onKeyword(e) {
    this.setData({ keyword: e.detail.value })
  },

  onSearch() {
    this.reload()
  },

  onClearKeyword() {
    this.setData({ keyword: '' })
    this.reload()
  },

  onCategory(e) {
    const id = e.currentTarget.dataset.id || ''
    if (id === this.data.categoryId) return
    // 切分类时右栏要回到顶部，否则会停在上一个分类的滚动位置
    this.setData({ categoryId: id, scrollTop: 0 })
    this.reload()
  },

  onProduct(e) {
    wx.navigateTo({ url: `/pages/product/product?id=${e.currentTarget.dataset.id}` })
  },

  /** 列表行上的「+」。整行是跳详情，加号要单独拦下点击，别把两个动作串了 */
  onAdd(e) {
    const productId = e.currentTarget.dataset.id
    api
      .request({
        url: '/api/shop/cart',
        method: 'POST',
        data: { productId, quantity: 1 }
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

  onShareAppMessage() {
    return {
      title: 'ANTONY CASA · 安玺·集',
      path: '/pages/shop/shop',
      imageUrl: homeMedia.shareImage()
    }
  }
})
