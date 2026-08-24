// 企业认证。填公司全称 + 联系人 + 手机号提交，进人工审核队列（需求文档 4.3.2）。
//
// 一页四态：没交过 / 审核中 / 已驳回 / 已认证。四态共用一个页面而不是拆成几页，
// 是因为它们回答的是同一个问题——「我的认证现在到哪一步了」。
const api = require('../../utils/api.js')

// 与服务端 app/anjia/certifications.py 的状态取值逐字一致
const PENDING = 'pending'
const APPROVED = 'approved'
const REJECTED = 'rejected'

// 与服务端模型、库里的 CHECK 同一条正则
const PHONE = /^1[3-9]\d{9}$/

function view(user) {
  const cert = (user && user.certification) || null
  const status = cert ? cert.status : ''
  return {
    user,
    cert,
    status,
    isApproved: status === APPROVED,
    isPending: status === PENDING,
    isRejected: status === REJECTED,
    // 已认证与审核中之外都能填表：没交过、被驳回、被撤销，都是可以（重新）提交的
    canSubmit: status !== APPROVED && status !== PENDING
  }
}

Page({
  data: {
    ...view(api.getUser()),
    form: { companyName: '', contactName: '', contactPhone: '' },
    error: '',
    loading: false,
    submitting: false
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
        console.error('[certification] 读取用户失败', err)
        this.setData({ error: err.message || '读取失败，请稍后再试', loading: false })
      })
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value, error: '' })
  },

  onSubmit() {
    if (this.data.submitting) return

    const { companyName, contactName, contactPhone } = this.data.form
    // 在本地先挡一道。服务端与数据库都各有一道同样的校验，这里只是为了让用户
    // 当场知道哪儿填错了，而不是等一个来回
    if (companyName.trim().length < 2) {
      return this.setData({ error: '请填写完整的公司名称' })
    }
    if (!contactName.trim()) {
      return this.setData({ error: '请填写联系人姓名' })
    }
    if (!PHONE.test(contactPhone.trim())) {
      return this.setData({ error: '联系电话格式不正确' })
    }

    this.setData({ submitting: true, error: '' })
    api
      .submitCertification({ companyName, contactName, contactPhone })
      .then(user => {
        this.setData({ ...view(user), submitting: false })
        wx.showToast({ title: '已提交', icon: 'success' })
      })
      .catch(err => {
        console.error('[certification] 提交失败', err)
        this.setData({ error: err.message || '提交失败，请稍后再试', submitting: false })
      })
  },

  onRetry() {
    this.refresh()
  }
})
