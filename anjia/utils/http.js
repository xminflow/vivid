// 请求传输层：把「发一个请求给我们自己的后端」这件事收在一处。
//
// 只有一条链路：wx.request 打 HTTP_BASE。打哪个地址由 config.js 按 envVersion
// 定完了，这里不再判断。
//
// 后端是一个进程装整个小程序矩阵，安家立业的接口全在 /api/anjia/ 下——
// 调用方传的 url 要带这个前缀，见 utils/api.js。

const { HTTP_BASE } = require('./config.js')

function sendByRequest(options) {
  // 地址为空 = 这个场景还没有可用的后端（预览、真机调试、体验版、正式版都还没有，
  // 见 config.js 的 DEV_BASE / SERVER_BASE）。在这里挡而不是在 config.js 里抛：
  // 那边是模块加载期，抛出去整个小程序白屏；这里只让这一个请求失败，页面照常渲染。
  //
  // 也**不降级**到安东尼之家的域名：那是另一个 appid 的后端，code2session 换不出
  // openid，报错却长得像微信的问题。
  if (!HTTP_BASE) {
    return Promise.reject(new Error('安家立业尚未配置服务器地址，请在开发者工具中调试'))
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${HTTP_BASE}${options.url}`,
      method: options.method || 'GET',
      header: options.header,
      data: options.data,
      success: res => resolve({ statusCode: res.statusCode, data: res.data || {} }),
      fail: err => {
        // 合法域名没配、证书有问题、域名解析不了都收敛成这一个 fail，只有微信给的
        // errMsg 才说得清是哪种。不打出来的话线上只剩一句「网络异常」，没法查
        console.error('[http] request 失败', options.url, err)
        reject(new Error('网络异常，请检查后重试'))
      }
    })
  })
}

/**
 * 发一个请求给自己的后端。
 * url 传业务路径（以 /api/anjia 开头），返回 { statusCode, data }。
 * 不判断业务成败，成败由调用方按接口语义判断。
 */
function send(options) {
  const header = { 'Content-Type': 'application/json', ...options.header }
  return sendByRequest({ ...options, header })
}

module.exports = { send }
