// 我的预约记录。
//
// 服务端的 GET /api/appointments 是后台用的全量列表，前端不能碰——那会把别人的
// 姓名电话露给所有人。这里走 /api/users/me/appointments，服务端按 token 认出是谁，
// 只返回他自己的。
//
// 本机那份是兜底：没网、或者提交时还没拿到登录态的记录，至少用户自己看得见。

const api = require('./api.js')

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

// 服务端那份。失败就退回本机的，页面不用分辨来源
function fetchMine() {
  return api
    .request({ url: '/api/users/me/appointments' })
    .then(res => {
      if (res.statusCode !== 200 || !res.data.ok) throw new Error(res.data.message || '')
      return res.data.items
    })
    .catch(() => null)
}

module.exports = { list, save, fetchMine }
