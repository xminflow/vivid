// app.js
App({
  onLaunch() {
    // 一期没有后端，登录只占个位：code 换 openId 等接口就绪后再补
    wx.login({
      success: () => {}
    })
  },
  globalData: {
    userInfo: null
  }
})
