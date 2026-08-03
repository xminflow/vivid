// app.js
App({
  onLaunch() {
    wx.login({
      success: () => {
        // 后端就绪后在这里用 res.code 换 openId
      }
    })
  },
  globalData: {
    userInfo: null
  }
})
