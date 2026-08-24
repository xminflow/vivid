// 拉起微信支付并确认结果。结算页、订单详情、订单列表三处共用。
//
// ## 为什么支付成功之后还要轮询
//
// `wx.requestPayment` 的 success 只表示「**用户把钱付出去了**」，不表示我们
// 已经知道这件事。真正让订单从待付款变成待发货的是服务端，而它有两条通道：
//   1. 微信的支付结果回调（POST /api/shop/pay/notify）—— 通常几百毫秒内到
//   2. 我们主动向微信查单（POST /api/shop/orders/{id}/sync-pay）—— 回调迟到时的兜底
//
// 所以这里在 success 之后轮询服务端，直到它说状态变了。轮询打的是 sync-pay：
// 服务端对「向微信查单」有 30 秒节流，所以这一串轮询里最多只有第一次真的去问
// 微信，其余几次读的是库里的状态——而回调如果先到，库里已经是新状态了。
//
// ## 轮询耗尽不算失败
//
// 8 次 × 1.2 秒 ≈ 10 秒。耗尽仍是待付款时提示「支付处理中」而**不是**报错：
// 钱可能真的已经扣了，只是回调和查单都还没赶上。告诉用户「支付失败」会让他
// 再付一次，那才是真的事故。

const api = require('./api.js')

// 与服务端 wxpay.QUERY_THROTTLE_SECONDS 无关——那是服务端对微信的节流，
// 这里是客户端问服务端的节奏。参照 weelume-base 的既有做法
const POLL_TIMES = 8
const POLL_INTERVAL_MS = 1200

/** 调起微信收银台。用户取消不是异常，单独用 cancelled 表示。 */
function requestPayment(params) {
  return new Promise((resolve, reject) => {
    wx.requestPayment({
      timeStamp: params.timeStamp,
      nonceStr: params.nonceStr,
      package: params.package,
      signType: params.signType,
      paySign: params.paySign,
      success: () => resolve('submitted'),
      fail: err => {
        const msg = (err && err.errMsg) || ''
        // 用户自己点了取消。不弹错误提示——他知道自己做了什么
        if (msg.indexOf('cancel') >= 0) {
          resolve('cancelled')
          return
        }
        console.error('[pay] requestPayment 失败', msg)
        reject(new Error(msg || '支付失败'))
      }
    })
  })
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/** 轮询服务端直到订单不再是待付款。返回最终状态（可能仍是 pending_pay）。 */
async function waitForPaid(orderId) {
  let status = 'pending_pay'
  for (let i = 0; i < POLL_TIMES; i++) {
    await sleep(POLL_INTERVAL_MS)
    try {
      const res = await api.request({
        url: `/api/shop/orders/${orderId}/sync-pay`,
        method: 'POST'
      })
      if (res.statusCode === 200 && res.data && res.data.ok) {
        status = res.data.status
        if (status !== 'pending_pay') return status
      }
    } catch (err) {
      // 单次查询失败不中断轮询：网络抖一下不该让用户以为没付成功
      console.error('[pay] 查询支付结果失败，继续重试', err)
    }
  }
  return status
}

/**
 * 拉起支付并等待结果。
 *
 * @param {string} orderId
 * @param {object} payParams  下单或 /pay 接口返回的那一组
 * @returns {Promise<'paid'|'cancelled'|'pending'>}
 *   paid      服务端已确认收到钱
 *   cancelled 用户主动取消，订单还在，可以再付
 *   pending   钱可能已经付了，但服务端还没确认（回调与查单都没赶上）
 */
async function payAndConfirm(orderId, payParams) {
  const outcome = await requestPayment(payParams)
  if (outcome === 'cancelled') return 'cancelled'

  wx.showLoading({ title: '确认支付结果', mask: true })
  try {
    const status = await waitForPaid(orderId)
    return status === 'pending_pay' ? 'pending' : 'paid'
  } finally {
    wx.hideLoading()
  }
}

/**
 * 对已存在的待付款订单重新拉起支付（订单详情、订单列表用）。
 * 每次都向服务端要一组新的支付参数——prepay_id 只有 2 小时有效期，
 * 存下来复用会在过期后得到一个「支付失败」，而用户看不出为什么。
 */
async function payExistingOrder(orderId) {
  const res = await api.request({ url: `/api/shop/orders/${orderId}/pay`, method: 'POST' })
  if (res.statusCode !== 200 || !res.data || !res.data.ok) {
    throw new Error((res.data && res.data.message) || '拉起支付失败')
  }
  return payAndConfirm(orderId, res.data.payParams)
}

/** 三种结果对应的提示。集中在这里，免得三个页面各写一套措辞。 */
function toast(outcome) {
  if (outcome === 'paid') {
    wx.showToast({ title: '支付成功', icon: 'success' })
    return
  }
  if (outcome === 'cancelled') {
    // 不提示。用户自己取消的，弹一句反而像出错了
    return
  }
  wx.showModal({
    title: '支付处理中',
    content: '如果你已经完成支付，稍后下拉刷新即可看到最新状态。请不要重复支付。',
    showCancel: false
  })
}

module.exports = { payAndConfirm, payExistingOrder, toast }
