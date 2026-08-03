// app.js
const api = require('./utils/api.js')

App({
  onLaunch() {
    // 静默登录：换 openid 建号、拿 token。失败不弹提示——
    // 首页是纯浏览，没有登录态也能看，真正要登录的接口自己会再登一次
    api.login().catch(() => {})
  },
  globalData: {
    userInfo: null
  }
})
