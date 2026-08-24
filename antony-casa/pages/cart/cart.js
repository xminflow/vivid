// 购物车。勾选、改数量、删除、清理失效。
//
// 车存在服务端、按人存，所以这一页**必须登录**（走 api.request，401 会自动重登一次）。
//
// 价格不存在车里：每次进来都实时回查商品的当前价格与在售状态。所以「三天前加购时
// 便宜」这种情况不存在——看到的永远是现价。下架的商品照样列出来但置灰、不可勾选，
// 不悄悄移除：用户会以为自己没加过。
//
// 结算按钮目前只到「选好了」为止——下单与支付是后续阶段，还没有接口。
const api = require('../../utils/api.js')
const { yuan } = require('../../utils/money.js')

Page({
  data: {
    items: [],
    loading: true,
    // 勾选的行 id。默认全选可用的行
    selected: [],
    selectedCount: 0,
    totalText: '0.00',
    allSelected: false,
    hasInvalid: false,
    // 正在提交某一行的改动，期间禁用该行按钮，避免连点把数量改乱
    busyId: ''
  },

  onShow() {
    this.load()
  },

  load() {
    this.setData({ loading: true })
    return api
      .request({ url: '/api/shop/cart' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        const items = res.data.items.map(item => ({ ...item, price: yuan(item.priceCents) }))
        // 保留上一次的勾选，新加进来的可用行默认勾上；失效行一律不勾
        const previous = this.data.selected
        const first = this.data.items.length === 0
        const selected = items
          .filter(item => item.available && (first || previous.indexOf(item.id) >= 0))
          .map(item => item.id)
        this.setData({ items, hasInvalid: items.some(item => !item.available) })
        this.applySelection(selected)
      })
      .catch(err => {
        console.error('购物车加载失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ loading: false })
      })
  },

  /** 勾选变了就重算合计。金额用整数分累加，最后一步才换算成元。
   *
   * 勾没勾中要写进每一行的 checked 字段：WXML 的表达式不支持方法调用，
   * 模板里写不了 selected.indexOf(item.id)——写了在工具里可能不报错，真机上是空的。
   */
  applySelection(selected) {
    let cents = 0
    let count = 0
    const items = this.data.items.map(item => {
      const checked = selected.indexOf(item.id) >= 0
      if (checked) {
        cents += item.priceCents * item.quantity
        count += item.quantity
      }
      return { ...item, checked }
    })
    const available = items.filter(item => item.available)
    this.setData({
      items,
      selected,
      selectedCount: count,
      totalText: yuan(cents),
      allSelected: available.length > 0 && selected.length === available.length
    })
  },

  onToggle(e) {
    const id = e.currentTarget.dataset.id
    const item = this.data.items.find(row => row.id === id)
    if (!item || !item.available) return
    const selected = this.data.selected.slice()
    const at = selected.indexOf(id)
    if (at >= 0) selected.splice(at, 1)
    else selected.push(id)
    this.applySelection(selected)
  },

  onToggleAll() {
    if (this.data.allSelected) {
      this.applySelection([])
      return
    }
    this.applySelection(this.data.items.filter(item => item.available).map(item => item.id))
  },

  onStep(e) {
    const { id, step } = e.currentTarget.dataset
    const item = this.data.items.find(row => row.id === id)
    if (!item || this.data.busyId) return

    const next = item.quantity + Number(step)
    // 减到 0 不是删除：删除是显式动作，走「删除」按钮。这里直接不动
    if (next < 1) return
    if (next > 99) {
      wx.showToast({ title: '单件最多 99 件', icon: 'none' })
      return
    }

    this.setData({ busyId: id })
    api
      .request({ url: `/api/shop/cart/${id}`, method: 'PUT', data: { quantity: next } })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '修改失败')
        }
        const items = this.data.items.map(row =>
          row.id === id ? { ...row, quantity: res.data.quantity } : row
        )
        this.setData({ items })
        this.applySelection(this.data.selected)
      })
      .catch(err => {
        console.error('修改购物车数量失败', err)
        wx.showToast({ title: err.message || '修改失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ busyId: '' })
      })
  },

  onRemove(e) {
    const id = e.currentTarget.dataset.id
    const item = this.data.items.find(row => row.id === id)
    if (!item) return

    wx.showModal({
      title: '移出购物车',
      content: `确定移出「${item.title}」？`,
      success: res => {
        if (!res.confirm) return
        api
          .request({ url: `/api/shop/cart/${id}`, method: 'DELETE' })
          .then(r => {
            if (r.statusCode !== 200 || !r.data || !r.data.ok) {
              throw new Error((r.data && r.data.message) || '移出失败')
            }
            this.load()
          })
          .catch(err => {
            console.error('移出购物车失败', err)
            wx.showToast({ title: err.message || '移出失败', icon: 'none' })
          })
      }
    })
  },

  onClearInvalid() {
    api
      .request({ url: '/api/shop/cart/invalid', method: 'DELETE' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '清理失败')
        }
        wx.showToast({ title: `已清理 ${res.data.removed} 件`, icon: 'none' })
        this.load()
      })
      .catch(err => {
        console.error('清理失效商品失败', err)
        wx.showToast({ title: err.message || '清理失败', icon: 'none' })
      })
  },

  onProduct(e) {
    const item = this.data.items.find(row => row.id === e.currentTarget.dataset.id)
    if (!item || !item.available) return
    wx.navigateTo({ url: `/pages/product/product?id=${item.productId}` })
  },

  /** 去结算。把勾中的**购物车行 id** 带过去，结算页自己回查一次当前价格与在售状态。
   *
   * 不把商品数据传过去：用户可能已经在这一页停留了几分钟，那份数据未必还准。
   * 传 id 让下一页重新拉，是这两页之间唯一可靠的契约。
   */
  onCheckout() {
    if (!this.data.selected.length) {
      wx.showToast({ title: '请先选择商品', icon: 'none' })
      return
    }
    wx.navigateTo({
      url: `/pages/checkout/checkout?from=cart&ids=${this.data.selected.join(',')}`
    })
  },

  onGoShopping() {
    wx.navigateBack()
  }
})
