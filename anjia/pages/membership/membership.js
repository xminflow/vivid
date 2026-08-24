// 会员中心。一期免费、自助开通、不分等级，只有是 / 否两种状态（需求文档 2.2）。
//
// 页面上那句「未来将会收费」是需求硬性要求的，不是文案润色：一期免费的目的是先聚
// 用户，二期要收费，事先没说清就会变成信誉问题。删这句之前先去看需求文档 2.2。
const api = require('../../utils/api.js')

// 到期日只给人看。不引日期库：这是这个小程序里唯一一处要格式化日期的地方
function toDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  // 解析不了就退回截取，宁可显示 UTC 那天，也不要显示 NaN
  if (isNaN(d.getTime())) return iso.slice(0, 10)
  const pad = n => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function view(user) {
  return {
    user,
    isMember: !!(user && user.isMember),
    expiresAt: toDate(user && user.memberExpiresAt)
  }
}

Page({
  data: {
    // 先用本机快照渲染，不让用户盯着空白等网络
    ...view(api.getUser()),
    error: '',
    loading: false,
    joining: false
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
        console.error('[membership] 读取用户失败', err)
        this.setData({ error: err.message || '读取失败，请稍后再试', loading: false })
      })
  },

  onJoin() {
    // 已经是会员就不再发请求。接口那边是幂等的，这里挡一道只是省一次往返
    if (this.data.joining || this.data.isMember) return

    this.setData({ joining: true, error: '' })
    api
      .joinMembership()
      .then(user => {
        this.setData({ ...view(user), joining: false })
        wx.showToast({ title: '已开通', icon: 'success' })
      })
      .catch(err => {
        // 不静默吞掉：用户点了开通却没反应，会一直点
        console.error('[membership] 开通失败', err)
        this.setData({ error: err.message || '开通失败，请稍后再试', joining: false })
      })
  },

  onRetry() {
    this.refresh()
  }
})
