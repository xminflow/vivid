// 结算页：选地址、核对订单行、提交订单。
//
// 两种来源，靠 query 区分：
//   from=cart      购物车勾选的行，ids 是购物车行 id（不是商品 id）
//   from=direct    商品详情页「立即购买」，带 productId 与 quantity
// source 会原样提交给服务端，它只决定**支付成功后要不要清购物车**——
// 详情页直接买不该清掉用户车里的同款。
//
// **金额只作展示**。提交时不传任何金额，总额由服务端按商品当前价格重算。
// 所以这一页显示的合计有可能和最终订单不一致（用户停留期间运营改了价），
// 那种情况下服务端算出来的才算数，下单成功后跳订单详情会看到真实金额。
//
// 提交成功后**不 navigateBack**，而是 redirectTo 订单详情：返回等于回到结算页，
// 用户很容易再点一次提交，下出第二单。
//
// 下单与支付是**两件事**：订单先建出来（服务端已提交事务），再拉起微信收银台。
// 所以支付环节无论发生什么——用户取消、微信报错、轮询超时——都不会让订单消失，
// 也一律不提示「下单失败」。用户可以在订单详情里点「去支付」接着付。
const api = require('../../utils/api.js')
const { yuan } = require('../../utils/money.js')
const pay = require('../../utils/pay.js')

Page({
  data: {
    // 由地址簿页在选中时写进来（见 pages/address/address.js 的 onPick）
    address: null,
    items: [],
    totalText: '0.00',
    source: 'cart',
    loading: true,
    submitting: false,
    // 发货通知的订阅消息模板 ID，从服务端取。空串表示没配，那就不请求授权
    shipNoticeTemplateId: ''
  },

  onLoad(query) {
    const from = (query && query.from) === 'direct' ? 'direct' : 'cart'
    this.setData({ source: from })
    this.loadSettings()

    if (from === 'direct') {
      this.loadDirect(query.productId, Number(query.quantity) || 1)
      return
    }
    // 购物车来源：ids 是逗号分隔的购物车行 id
    this.loadFromCart(((query && query.ids) || '').split(',').filter(Boolean))
  },

  onShow() {
    // 地址可能在本页停留期间被改过（用户去地址簿编辑了），回来要刷新。
    // address 已经选中时只补一次，避免覆盖用户刚选的那条
    if (!this.data.address) this.loadDefaultAddress()
  },

  /** 取发货通知的模板 ID。从服务端拿而不是写死在小程序里：
   * 换模板时只改服务端配置，不用重新发版，也不会出现两边对不上
   * （那种错的表现是「用户点了允许但收不到通知」，极难查）。 */
  loadSettings() {
    return api
      .requestOptionalAuth({ url: '/api/shop/settings' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) return
        this.setData({ shipNoticeTemplateId: res.data.shipNoticeTemplateId || '' })
      })
      .catch(err => {
        // 取不到就不要授权，下单照常。通知是锦上添花，不该挡住交易
        console.error('设置加载失败，跳过发货通知授权', err)
      })
  },

  /** 没选过地址时用默认地址。列表接口把默认地址排在第一条。 */
  loadDefaultAddress() {
    return api
      .request({ url: '/api/shop/addresses' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) return
        const items = res.data.items || []
        const preferred = items.find(item => item.isDefault)
        // 没有默认地址就不自动选：让用户自己挑，免得寄到一个他没想寄的地方
        if (preferred) this.setData({ address: preferred })
      })
      .catch(err => {
        // 地址加载失败不该挡住整页，用户还能手动去选
        console.error('默认地址加载失败', err)
      })
  },

  /** 从购物车取选中的那几行。回查一次而不是让上一页传过来：
   * 价格和在售状态必须是此刻的，上一页的数据可能已经停留了几分钟。 */
  loadFromCart(ids) {
    if (!ids.length) {
      this.setData({ loading: false })
      wx.showToast({ title: '没有选中的商品', icon: 'none' })
      return
    }
    api
      .request({ url: '/api/shop/cart' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        const chosen = res.data.items.filter(item => ids.indexOf(item.id) >= 0)
        const usable = chosen.filter(item => item.available)
        // 停留期间被下架的，这里就少了。明确提示，不静默少买一件
        if (usable.length !== chosen.length) {
          wx.showToast({ title: '部分商品已下架，已移除', icon: 'none' })
        }
        this.apply(usable)
      })
      .catch(err => {
        console.error('结算页加载购物车失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
        this.setData({ loading: false })
      })
  },

  /** 「立即购买」：只有一件商品，直接查详情。 */
  loadDirect(productId, quantity) {
    if (!productId) {
      this.setData({ loading: false })
      return
    }
    api
      .requestOptionalAuth({ url: `/api/shop/products/${productId}` })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '商品已下架')
        }
        const item = res.data.item
        this.apply([
          {
            productId: item.id,
            title: item.title,
            priceCents: item.priceCents,
            cover: (item.images || [])[0] || '',
            quantity
          }
        ])
      })
      .catch(err => {
        console.error('结算页加载商品失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
        this.setData({ loading: false })
      })
  },

  /** 统一算合计并落到 data。金额用整数分累加，最后一步才换算成元。 */
  apply(rows) {
    const items = rows.map(row => ({ ...row, price: yuan(row.priceCents) }))
    const cents = items.reduce((sum, row) => sum + row.priceCents * row.quantity, 0)
    this.setData({ items, totalText: yuan(cents), loading: false })
  },

  onPickAddress() {
    wx.navigateTo({ url: '/pages/address/address?pick=1' })
  },

  /** 要一次「发货通知」的订阅授权。
   *
   * 订阅消息是一次性的：用户点一次「允许」，服务端就获得一次发送额度，发完即止。
   * 所以要在**提交订单这一刻**要——此刻用户最有动机同意；等到发货时再要，
   * 人早就离开小程序了，根本没有弹窗的机会。
   *
   * 拒绝是常态，不能挡住下单。所以这里无论成败都 resolve，绝不 reject。
   */
  requestShipNotice() {
    const templateId = this.data.shipNoticeTemplateId
    if (!templateId) return Promise.resolve()
    return new Promise(resolve => {
      wx.requestSubscribeMessage({
        tmplIds: [templateId],
        success: res => {
          console.info('[checkout] 发货通知订阅结果', res[templateId])
          resolve()
        },
        fail: err => {
          // 用户拒绝、或这个模板被小程序后台删了，都走这里。都不该影响下单
          console.warn('[checkout] 订阅发货通知失败', err && err.errMsg)
          resolve()
        }
      })
    })
  },

  async onSubmit() {
    if (this.data.submitting) return
    if (!this.data.address) {
      wx.showToast({ title: '请选择收货地址', icon: 'none' })
      return
    }
    if (!this.data.items.length) {
      wx.showToast({ title: '没有可结算的商品', icon: 'none' })
      return
    }

    // 必须在下单之前要授权：wx.requestSubscribeMessage 要求由用户点击直接触发，
    // 放在下单成功的回调里会因为「不是同步调用链」而弹不出来
    await this.requestShipNotice()

    this.setData({ submitting: true })
    api
      .request({
        url: '/api/shop/orders',
        method: 'POST',
        data: {
          addressId: this.data.address.id,
          source: this.data.source,
          // 只传「买什么、买几件」。金额一个字都不传，服务端自己算
          items: this.data.items.map(row => ({
            productId: row.productId,
            quantity: row.quantity
          }))
        }
      })
      .then(async res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '下单失败')
        }
        const order = res.data.order
        const payParams = res.data.payParams
        console.info('[checkout] 下单成功', order.orderNo, order.totalCents)

        // 订单已经建出来了。从这里往后**无论支付发生什么都不再报「下单失败」**——
        // 单子是有效的，用户可以在订单详情里再付。所以下面的异常只提示、不回退。
        if (payParams) {
          try {
            const outcome = await pay.payAndConfirm(order.id, payParams)
            pay.toast(outcome)
          } catch (err) {
            console.error('[checkout] 拉起支付失败', err)
            wx.showToast({ title: '支付未完成，可在订单里继续', icon: 'none' })
          }
        } else {
          // 服务端没给支付参数：支付未配置，或它向微信下单时失败了。
          // 订单停在待付款，详情页的「去支付」可以重试
          console.warn('[checkout] 没有支付参数，订单停在待付款', order.orderNo)
        }

        // redirectTo 而不是 navigateTo：留着结算页在栈里，用户返回后很容易再提交一次
        wx.redirectTo({ url: `/pages/order/order?id=${order.id}` })
      })
      .catch(err => {
        console.error('下单失败', err)
        wx.showToast({ title: err.message || '下单失败', icon: 'none' })
        this.setData({ submitting: false })
      })
  }
})
