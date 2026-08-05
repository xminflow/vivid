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

// 初始化只做一次。放在这里而不是 app.js：请求方是这一层，由它自己保证前置条件，
// 就不用担心页面请求早于 onLaunch 的时序问题。
// 缓存的是 Promise 而不是布尔量——共享环境的 init() 是异步的，用布尔量的话
// 启动时并发的几个请求会各自初始化一遍
let cloudPromise = null

/**
 * 拿到可用的 cloud 实例。两种形态：
 *   直连——环境就在本小程序名下，用全局的 wx.cloud
 *   共享——环境属于别的 appid，必须新建 Cloud 实例并 await init()，
 *         全局 wx.cloud 访问不到别人的环境
 */
function ensureCloud() {
  if (cloudPromise) return cloudPromise

  if (!wx.cloud) {
    // 基础库过低时 wx.cloud 不存在。不静默降级到 local——那只会在真机上
    // 变成一堆连不上 127.0.0.1 的网络错误，问题反而更难查
    return Promise.reject(new Error('当前微信版本过低，请升级后再试'))
  }

  if (CLOUD.resourceAppid) {
    const instance = new wx.cloud.Cloud({
      resourceAppid: CLOUD.resourceAppid,
      resourceEnv: CLOUD.env
    })
    cloudPromise = instance.init().then(() => instance)
  } else {
    wx.cloud.init({ env: CLOUD.env })
    cloudPromise = Promise.resolve(wx.cloud)
  }

  // 初始化失败要把缓存清掉，否则后面每个请求都拿到同一个已拒绝的 Promise，
  // 配置改对了也要重启小程序才生效
  cloudPromise.catch(() => {
    cloudPromise = null
  })

  return cloudPromise
}

function sendByCloud(options) {
  const params = {
    path: options.url,
    method: options.method || 'GET',
    header: { ...options.header, 'X-WX-SERVICE': CLOUD.service },
    data: options.data
  }
  // 共享环境的实例创建时已经绑定了环境，再传 config.env 反而会冲突
  if (!CLOUD.resourceAppid) params.config = { env: CLOUD.env }

  // 包一层 Promise：ensureCloud 和 callContainer 都可能同步抛（基础库不支持、
  // 云环境没关联）。同步抛会直接穿透调用方的 .catch，把事件回调整个打断
  return Promise.resolve()
    .then(() => ensureCloud())
    .then(cloud =>
      cloud.callContainer(params).catch(err => {
        // 云托管把容器没起来、服务名不对、没授权都抛成同一个 reject，
        // 统一成用户能看懂的一句，原始信息留在控制台供排查
        console.error('[http] callContainer 失败', options.url, err)
        throw new Error('网络异常，请检查后重试')
      })
    )
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

  return API_MODE === 'cloud' ? sendByCloud(req) : sendByRequest(req)
}

module.exports = { send }
