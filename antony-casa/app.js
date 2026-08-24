// app.js
const api = require('./utils/api.js')
const { API_MODE, ENV_VERSION, HTTP_BASE } = require('./utils/config.js')

App({
  onLaunch() {
    // 启动就把「这一次连的是哪个环境」打出来。
    // 环境是自动判断的（见 utils/config.js），判断结果不打出来就只能靠猜——
    // 「本地看不到数据」十有八九是环境选得不对，或者工具的「不校验合法域名」没勾，
    // 有这一行能一眼分辨，不用去翻 Network 面板
    console.info(`[env] envVersion=${ENV_VERSION || '(读不到)'} mode=${API_MODE} base=${HTTP_BASE}`)

    // 静默登录：换 openid 建号、拿 token。失败不弹提示——
    // 首页是纯浏览，没有登录态也能看，真正要登录的接口自己会再登一次
    api.login().catch(() => {})
  },
  globalData: {
    userInfo: null
  }
})
