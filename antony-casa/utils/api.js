// 跟服务端说话的那一层：请求 + 登录态。
//
// 登录是静默的：wx.login 拿 code，服务端换 openid 建号，回一个 token。
// 全程不弹授权框——openid 不需要用户点头，头像昵称才需要，那部分暂时不取。
// token 存在本机，每个请求自动带上；过期了（401）自动重登一次再重试。

const { API_BASE } = require('./config.js')

const TOKEN_KEY = 'authToken'
const USER_KEY = 'authUser'

// 同一时刻只登一次。启动时几个页面一起发请求，不该换出几个 code 来
let pendingLogin = null

function getToken() {
  try {
    return wx.getStorageSync(TOKEN_KEY) || ''
  } catch (e) {
    return ''
  }
}

// 用户信息在服务端，这里存的只是上次的快照，用来首屏先渲染出来
function getUser() {
  try {
    return wx.getStorageSync(USER_KEY) || null
  } catch (e) {
    return null
  }
}

function setSession(token, user) {
  try {
    wx.setStorageSync(TOKEN_KEY, token)
    wx.setStorageSync(USER_KEY, user || null)
  } catch (e) {
    // 存不进去也不致命，这次启动内存里还有 token
  }
}

function clearToken() {
  try {
    wx.removeStorageSync(TOKEN_KEY)
  } catch (e) {}
}

// 裸请求。不判断业务成败，只把 wx.request 变成 Promise
function raw(options) {
  const header = { 'Content-Type': 'application/json' }
  if (options.token) header.Authorization = `Bearer ${options.token}`

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${options.url}`,
      method: options.method || 'GET',
      header,
      data: options.data,
      success: res => resolve({ statusCode: res.statusCode, data: res.data || {} }),
      fail: () => reject(new Error('网络异常，请检查后重试'))
    })
  })
}

function wxLogin() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: res => (res.code ? resolve(res.code) : reject(new Error('登录失败，请稍后再试'))),
      fail: () => reject(new Error('登录失败，请稍后再试'))
    })
  })
}

// 换一个新 token。并发调用共用同一次请求
function login() {
  if (pendingLogin) return pendingLogin

  pendingLogin = wxLogin()
    .then(code => raw({ url: '/api/auth/login', method: 'POST', data: { code } }))
    .then(res => {
      if (res.statusCode !== 200 || !res.data.ok) {
        throw new Error(res.data.message || '登录失败，请稍后再试')
      }
      setSession(res.data.token, res.data.user)
      return res.data.token
    })
    .then(
      token => {
        pendingLogin = null
        return token
      },
      err => {
        pendingLogin = null
        throw err
      }
    )

  return pendingLogin
}

function ensureToken() {
  const token = getToken()
  return token ? Promise.resolve(token) : login()
}

// 带登录态的请求。token 过期时重登一次再重试，用户无感
function request(options) {
  return ensureToken()
    .then(token => raw({ ...options, token }))
    .then(res => {
      if (res.statusCode !== 401) return res
      clearToken()
      return login().then(token => raw({ ...options, token }))
    })
}

// 未登录也能用的接口走这个：有登录态就带上（服务端好记归属），
// 登不上也照样发出去。预约表单不能因为登录出问题就交不了
function requestOptionalAuth(options) {
  return ensureToken()
    .catch(() => '')
    .then(token => raw({ ...options, token }))
}

module.exports = { request, requestOptionalAuth, raw, login, getToken, getUser }
