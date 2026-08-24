// 我的订单。tab：全部 / 待付款 / 待发货 / 待收货。
//
// 状态值与服务端 models.ORDER_STATUSES、schema.sql 的 CHECK 逐字一致，
// 改一处要改三处。传错服务端直接 400，不会带着脏值查库返回空列表——
// 那样这一页会显示「没有订单」，看不出是前端传错了。
//
// 列表接口把订单行一起带回来了，所以每张卡能直接显示缩略图，不用再发一轮请求。
const api = require('../../utils/api.js')
const { yuan } = require('../../utils/money.js')
const pay = require('../../utils/pay.js')

// 与服务端状态一一对应。closed / refunded 不单独开 tab（量少），在「全部」里看
const TABS = [
  { key: '', text: '全部' },
  { key: 'pending_pay', text: '待付款' },
  { key: 'pending_ship', text: '待发货' },
  { key: 'pending_receive', text: '待收货' }
]

const STATUS_TEXT = {
  pending_pay: '待付款',
  pending_ship: '待发货',
  pending_receive: '待收货',
  completed: '已完成',
  closed: '已关闭',
  refunded: '已退款'
}

const PAGE_SIZE = 10

Page({
  data: {
    tabs: TABS,
    active: '',
    items: [],
    page: 1,
    total: 0,
    loading: true,
    // 还有没有下一页。上拉到底时据此决定要不要再请求
    hasMore: false,
    // 正在支付的那一单。期间禁掉它的按钮，避免连点拉起两次收银台
    payingId: ''
  },

  onLoad(query) {
    // 从订单详情跳回来时可以指定落在哪个 tab
    const status = (query && query.status) || ''
    this.setData({ active: TABS.some(tab => tab.key === status) ? status : '' })
  },

  // 取消订单、支付返回后都要看到最新状态，所以放 onShow
  onShow() {
    this.reload()
  },

  onTab(e) {
    const key = e.currentTarget.dataset.key
    if (key === this.data.active) return
    this.setData({ active: key })
    this.reload()
  },

  reload() {
    this.setData({ page: 1, items: [] })
    return this.fetch()
  },

  onReachBottom() {
    if (this.data.loading || !this.data.hasMore) return
    this.setData({ page: this.data.page + 1 })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true })
    const data = { page: this.data.page, pageSize: PAGE_SIZE }
    if (this.data.active) data.status = this.data.active

    return api
      .request({ url: '/api/shop/orders', data })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        const rows = res.data.items.map(order => ({
          ...order,
          statusText: STATUS_TEXT[order.status] || order.status,
          totalText: yuan(order.totalCents),
          // 卡片上只露前三件，再多用「等 N 件」概括，否则一单十几行会把列表撑爆
          preview: (order.items || []).slice(0, 3),
          quantity: (order.items || []).reduce((sum, row) => sum + row.quantity, 0),
          canPay: order.status === 'pending_pay'
        }))
        const items = this.data.page === 1 ? rows : this.data.items.concat(rows)
        this.setData({
          items,
          total: res.data.total,
          hasMore: items.length < res.data.total
        })
      })
      .catch(err => {
        console.error('订单列表加载失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ loading: false })
      })
  },

  onOrder(e) {
    wx.navigateTo({ url: `/pages/order/order?id=${e.currentTarget.dataset.id}` })
  },

  /** 列表里直接付。省掉「点进详情再点去支付」这一步——待付款的单，
   * 用户点进列表多半就是为了把它付掉。 */
  onPay(e) {
    const id = e.currentTarget.dataset.id
    if (this.data.payingId) return
    this.setData({ payingId: id })
    pay
      .payExistingOrder(id)
      .then(outcome => {
        pay.toast(outcome)
        // 付成功后这一单要从「待付款」tab 里消失，所以整列表重拉
        this.reload()
      })
      .catch(err => {
        console.error('去支付失败', err)
        wx.showToast({ title: err.message || '拉起支付失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ payingId: '' })
      })
  },

  onCancel(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '取消订单',
      content: '取消后这笔订单会关闭，需要重新下单。',
      success: res => {
        if (!res.confirm) return
        api
          .request({ url: `/api/shop/orders/${id}/cancel`, method: 'POST' })
          .then(r => {
            if (r.statusCode !== 200 || !r.data || !r.data.ok) {
              throw new Error((r.data && r.data.message) || '取消失败')
            }
            wx.showToast({ title: '已取消', icon: 'none' })
            this.reload()
          })
          .catch(err => {
            console.error('取消订单失败', err)
            wx.showToast({ title: err.message || '取消失败', icon: 'none' })
          })
      }
    })
  },

  onGoShopping() {
    wx.switchTab({ url: '/pages/shop/shop' })
  }
})
