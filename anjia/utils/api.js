// 跟服务端说话的那一层：请求 + 登录态。
//
// 登录是静默的：wx.login 拿 code，服务端用安家立业自己的 appid 换 openid 建号，
// 回一个 token。全程不弹授权框——openid 不需要用户点头，头像昵称才需要。
// token 存在本机，每个请求自动带上；过期了（401）自动重登一次再重试。

const { send } = require('./http.js')

// 存储键带 anjia 前缀：开发者工具里几个小程序共用一台机器，
// 键重了会互相覆盖，表现是「登录态莫名其妙串了」
const TOKEN_KEY = 'anjia:authToken'
const USER_KEY = 'anjia:authUser'

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

// 裸请求。不判断业务成败，只负责带上登录态。
//
// 调用方给的 header 要留着：案例图上传发的是**裸的图片字节**，得自己声明
// Content-Type: application/octet-stream，在这里被覆盖掉的话服务端会按 JSON 解析
function raw(options) {
  const header = { ...options.header }
  if (options.token) header.Authorization = `Bearer ${options.token}`
  return send({ ...options, header })
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
    .then(code => raw({ url: '/api/anjia/auth/login', method: 'POST', data: { code } }))
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

// 取一个「返回完整用户对象」的接口的结果，顺手更新本机快照。
//
// 会改动身份的写接口（开通会员、提交企业认证）都回完整的用户对象、都走这里：
// 身份的更新因此只有这一条代码路径。若各接口只回自己那一小块，页面就得各写一份
// 合并逻辑，而身份是三层正交的（个人/企业认证/会员），合并漏一处就会出现
// 「开通了会员但页面还显示不是」这种只在某个页面复现的怪事。
function userRequest(options, failMessage) {
  return request(options).then(res => {
    if (res.statusCode !== 200 || !res.data.ok) {
      throw new Error(res.data.message || failMessage)
    }
    setSession(getToken(), res.data.user)
    return res.data.user
  })
}

// 取一个普通业务接口的结果。与 userRequest 的区别只在于它不碰本机的身份快照——
// 案例相关的接口不返回用户对象，套 userRequest 会把快照洗成 undefined
function dataRequest(options, failMessage) {
  return request(options).then(res => {
    if (res.statusCode !== 200 || !res.data.ok) {
      throw new Error(res.data.message || failMessage)
    }
    return res.data
  })
}

function me() {
  return userRequest({ url: '/api/anjia/users/me' }, '读取失败，请稍后再试')
}

// 开通会员。一期免费、即时生效；重复调用是幂等的，不会延长有效期
function joinMembership() {
  return userRequest(
    { url: '/api/anjia/users/me/membership', method: 'POST' },
    '开通失败，请稍后再试'
  )
}

// 提交企业认证申请。进的是人工审核队列，不是即时生效
function submitCertification(form) {
  return userRequest(
    { url: '/api/anjia/certifications', method: 'POST', data: form },
    '提交失败，请稍后再试'
  )
}

// ---------------------------------------------------------------- 案例

// 首页内容流。游标分页——内容流一直有新条目插到最前，用页码翻页会重复或漏条。
// cursor 为空就是第一页；返回的 nextCursor 为 null 说明到底了
function listCases(cursor) {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  return dataRequest({ url: `/api/anjia/cases${query}` }, '加载失败，请稍后再试')
}

// 案例详情。服务端顺带记一次浏览（同一个人只算一次，作者看自己的不算）
function readCase(id) {
  return dataRequest({ url: `/api/anjia/cases/${id}` }, '加载失败，请稍后再试').then(
    body => body.case
  )
}

// 发布一条案例。进的是人工审核队列，不是直接上首页
function createCase(form) {
  return dataRequest(
    { url: '/api/anjia/cases', method: 'POST', data: form },
    '发布失败，请稍后再试'
  ).then(body => body.case)
}

/**
 * 传一张案例图，返回 { key, url, w, h }。
 *
 * 发的是**裸的图片字节**而不是 wx.uploadFile 的 multipart：服务端解析 multipart
 * 要多装一个依赖，而这里一次只传一张（见 server/app/anjia/cases.py）。
 *
 * 宽高由服务端读文件头算出来并焊进对象键，这里拿到的是结果，不是自己量的——
 * 客户端自报宽高的话，瀑布流的版面就成了任人摆布的东西。
 */
function uploadCaseImage(filePath) {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      success: res => resolve(res.data),
      fail: err => {
        console.error('[api] 读取本地图片失败', err)
        reject(new Error('读取图片失败，请换一张'))
      }
    })
  }).then(buffer =>
    dataRequest(
      {
        url: '/api/anjia/cases/images',
        method: 'POST',
        header: { 'Content-Type': 'application/octet-stream' },
        data: buffer
      },
      '图片上传失败，请重试'
    )
  )
}

module.exports = {
  request,
  raw,
  login,
  me,
  joinMembership,
  submitCertification,
  listCases,
  readCase,
  createCase,
  uploadCaseImage,
  getToken,
  getUser
}
