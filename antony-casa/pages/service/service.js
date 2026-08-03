// 服务：五个服务各自成卡，点进去是该服务的申请表单
const { services } = require('../../mock/service.js')

Page({
  data: {
    services
  },

  onApply(e) {
    wx.navigateTo({ url: `/pages/apply/apply?id=${e.currentTarget.dataset.id}` })
  },

  onShareAppMessage() {
    return {
      title: 'ANTONY CASA · 全案设计服务',
      path: '/pages/service/service'
    }
  }
})
