// 请求传输层：把「发一个请求给我们自己的后端」这件事收在一处。
//
// 后端有两条链路，业务代码不该关心走的是哪条：
//   cloud —— 微信云托管。走 wx.cloud.callContainer，微信内网直连容器，
//            不需要备案域名，也不用在小程序后台配 request 合法域名
//   local —— 直连本机 WSL 里跑的服务。只有开发者工具能连 127.0.0.1，真机连不上
//
// 走哪条由 config.js 的 API_MODE 决定。COS 直传不走这里：那是外部域名的
// 绝对地址，两种模式下都得用 wx.request 直发（见 upload.js）。

const { API_MODE, CLOUD, API_BASE } = require('./config.js')

// wx.cloud.init 全局只需一次。放在这里而不是 app.js：请求方是这一层，
// 由它自己保证前置条件，就不用担心页面请求早于 onLaunch 的时序问题
let cloudReady = false

function ensureCloud() {
  if (cloudReady) return
  if (!wx.cloud) {
    // 基础库过低时 wx.cloud 不存在。不静默降级到 local——那只会在真机上
    // 变成一堆连不上 127.0.0.1 的网络错误，问题反而更难查
    throw new Error('当前微信版本过低，请升级后再试')
  }
  wx.cloud.init({ env: CLOUD.env })
  cloudReady = true
}

function sendByCloud(options) {
  ensureCloud()

  return wx.cloud
    .callContainer({
      config: { env: CLOUD.env },
      path: options.url,
      method: options.method || 'GET',
      header: { ...options.header, 'X-WX-SERVICE': CLOUD.service },
      data: options.data
    })
    .then(res => ({ statusCode: res.statusCode, data: res.data || {} }))
}

function sendByRequest(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${options.url}`,
      method: options.method || 'GET',
      header: options.header,
      data: options.data,
      success: res => resolve({ statusCode: res.statusCode, data: res.data || {} }),
      fail: () => reject(new Error('网络异常，请检查后重试'))
    })
  })
}

/**
 * 发一个请求给自己的后端。
 * url 传业务路径（以 / 开头），两种模式下都一样，调用方不用拼域名。
 * 返回 { statusCode, data }，不判断业务成败，成败由调用方按接口语义判断。
 */
function send(options) {
  const header = { 'Content-Type': 'application/json', ...options.header }
  const req = { ...options, header }

  return API_MODE === 'cloud'
    ? sendByCloud(req).catch(err => {
        // 云托管把各种失败（容器没起来、服务名不对、网络断）都抛成同一个 reject，
        // 这里统一成用户能看懂的一句，原始信息留在控制台供排查
        console.error('[http] callContainer 失败', options.url, err)
        throw new Error('网络异常，请检查后重试')
      })
    : sendByRequest(req)
}

module.exports = { send }
