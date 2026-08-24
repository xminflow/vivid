// 「我的」—— 登录态与身份的落点。三层身份（个人 / 企业认证 / 会员）在这里各占一行，
// 具体的开通与申请在各自的二级页里做，这一页只负责「我现在是谁」。
const api = require('../../utils/api.js')

// 入口行右侧那句话。企业认证有四种状态，都得说得出人话
const CERT_LABELS = {
  pending: { text: '审核中', on: false },
  approved: { text: '已认证', on: true },
  rejected: { text: '未通过', on: false },
  revoked: { text: '已撤销', on: false }
}

function view(user) {
  const cert = (user && user.certification) || null
  return {
    user,
    // 对外展示名：认证通过后是公司全称，否则是微信昵称。
    // 服务端不覆盖 nickname，所以认证一撤销这里自动落回，不需要任何补偿逻辑
    displayName: (user && (user.companyName || user.nickname)) || '（未授权）',
    certLabel: (cert && CERT_LABELS[cert.status]) || { text: '未认证', on: false }
  }
}

Page({
  data: {
    // 先用本机快照渲染，不让用户盯着空白等网络
    ...view(api.getUser()),
    error: '',
    loading: false
  },

  onShow() {
    this.refresh()
  },

  refresh() {
    this.setData({ loading: true, error: '' })
    api
      .me()
      .then(user => this.setData({ ...view(user), loading: false }))
      .catch(err => {
        // 不静默吞掉：这一页失败了就只剩一片空白，必须说清是哪一步没通
        console.error('[mine] 读取用户失败', err)
        this.setData({ error: err.message || '读取失败，请稍后再试', loading: false })
      })
  },

  onMembership() {
    wx.navigateTo({ url: '/pages/membership/membership' })
  },

  onCertification() {
    wx.navigateTo({ url: '/pages/certification/certification' })
  },

  onRetry() {
    this.refresh()
  }
})
