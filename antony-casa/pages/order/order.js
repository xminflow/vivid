// 订单详情：状态、订单行快照、收货地址、单号、物流信息、取消。
//
// 页面上所有的商品信息都是**下单那一刻的快照**（服务端存在 shop_order_items），
// 商品后来改价改名、图换了，这里都不变。这是刻意的：用户看到的必须是他下单时
// 看到的那个价格，否则一笔已付款的订单会显示出另一个金额。
const api = require('../../utils/api.js')
const { yuan } = require('../../utils/money.js')
const pay = require('../../utils/pay.js')

const STATUS_TEXT = {
  pending_pay: '待付款',
  pending_ship: '待发货',
  pending_receive: '待收货',
  completed: '已完成',
  closed: '已关闭',
  refunded: '已退款'
}

// 状态下面那行小字，说明「现在轮到谁做什么」
const STATUS_HINT = {
  pending_pay: '请尽快完成支付',
  pending_ship: '我们正在为你备货',
  pending_receive: '商品已发出，注意查收',
  completed: '交易已完成，感谢选择',
  closed: '订单已关闭',
  refunded: '款项已原路退回'
}

const CLOSE_REASON_TEXT = {
  timeout: '超时未支付，自动关闭',
  user_cancel: '你取消了这笔订单'
}

const SHIPPING_TEXT = {
  express: '快递发货',
  local: '同城配送',
  none: '无需物流'
}

Page({
  data: {
    id: '',
    order: null,
    loading: true,
    // 支付进行中。期间禁掉按钮，避免连点拉起两次收银台
    paying: false
  },

  onLoad(query) {
    const id = (query && query.id) || ''
    if (!id) {
      wx.showToast({ title: '订单不存在', icon: 'none' })
      this.setData({ loading: false })
      return
    }
    this.setData({ id })
  },

  onShow() {
    if (this.data.id) this.load()
  },

  load() {
    this.setData({ loading: true })
    return api
      .request({ url: `/api/shop/orders/${this.data.id}` })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        const order = res.data.order
        this.setData({
          order: {
            ...order,
            statusText: STATUS_TEXT[order.status] || order.status,
            statusHint:
              order.status === 'closed' && order.closeReason
                ? CLOSE_REASON_TEXT[order.closeReason] || STATUS_HINT.closed
                : STATUS_HINT[order.status] || '',
            totalText: yuan(order.totalCents),
            quantity: (order.items || []).reduce((sum, row) => sum + row.quantity, 0),
            items: (order.items || []).map(row => ({ ...row, price: yuan(row.priceCents) })),
            shippingText: order.shipping && SHIPPING_TEXT[order.shipping.type],
            canCancel: order.status === 'pending_pay'
          }
        })
      })
      .catch(err => {
        console.error('订单详情加载失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ loading: false })
      })
  },

  onCopyNo() {
    if (!this.data.order) return
    wx.setClipboardData({ data: this.data.order.orderNo })
  },

  /** 去支付。每次都向服务端要一组新的支付参数，理由见 utils/pay.js。 */
  onPay() {
    if (this.data.paying) return
    this.setData({ paying: true })
    pay
      .payExistingOrder(this.data.id)
      .then(outcome => {
        pay.toast(outcome)
        // 无论哪种结果都刷新一次：付成功了要看到新状态，
        // 取消了也要确认订单还在（比如刚好被超时关单扫描关掉了）
        this.load()
      })
      .catch(err => {
        console.error('去支付失败', err)
        wx.showToast({ title: err.message || '拉起支付失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ paying: false })
      })
  },

  onCancel() {
    wx.showModal({
      title: '取消订单',
      content: '取消后这笔订单会关闭，需要重新下单。',
      success: res => {
        if (!res.confirm) return
        api
          .request({ url: `/api/shop/orders/${this.data.id}/cancel`, method: 'POST' })
          .then(r => {
            if (r.statusCode !== 200 || !r.data || !r.data.ok) {
              throw new Error((r.data && r.data.message) || '取消失败')
            }
            wx.showToast({ title: '已取消', icon: 'none' })
            this.load()
          })
          .catch(err => {
            console.error('取消订单失败', err)
            wx.showToast({ title: err.message || '取消失败', icon: 'none' })
          })
      }
    })
  },

  onProduct(e) {
    // 商品可能已经下架，进去会是 404 页——那是详情页自己的事，这里照常跳
    wx.navigateTo({ url: `/pages/product/product?id=${e.currentTarget.dataset.id}` })
  }
})
