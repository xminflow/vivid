// 本机的预约记录。
//
// 服务端的 GET /api/appointments 是后台用的全量列表，没有按用户隔离，
// 前端不能直接拿来当「我的预约」——那会把别人的姓名电话露给所有人。
// 等 app.js 里的 wx.login 换到 openId、服务端补上按 openId 过滤的接口之后，
// 把 list() 换成请求即可，页面不用动。

const KEY = 'myAppointments'
const MAX = 20

function list() {
  try {
    const rows = wx.getStorageSync(KEY)
    return Array.isArray(rows) ? rows : []
  } catch (e) {
    return []
  }
}

// 新的排在前面，只留最近 MAX 条
function save(record) {
  try {
    const rows = [{ ...record, createdAt: Date.now() }, ...list()].slice(0, MAX)
    wx.setStorageSync(KEY, rows)
    return rows
  } catch (e) {
    return list()
  }
}

module.exports = { list, save }
