// 金额换算。整条链路上金额都是**整数分**：库里是 int、接口传的是 int、
// 和微信支付对账的也是分。只有显示给人看的这一刻才换算成元。
//
// 用整数除法再补零，不写 `cents / 100`：浮点看着没事，但金额一旦参与加减
// 就会攒出对账差，而这个板块接的是真实支付。

/** 1299900 → '12999.00' */
function yuan(cents) {
  const value = Number(cents)
  if (!Number.isFinite(value)) return '0.00'
  const negative = value < 0
  const abs = Math.abs(Math.round(value))
  const integer = Math.floor(abs / 100)
  const fraction = String(abs % 100).padStart(2, '0')
  return `${negative ? '-' : ''}${integer}.${fraction}`
}

module.exports = { yuan }
