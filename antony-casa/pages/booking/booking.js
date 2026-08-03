// 预约参观登记表。选项值必须与 server/schema.sql 里的 CHECK 约束一致。
const api = require('../../utils/api.js')
const appointments = require('../../utils/appointments.js')

const VISITOR_TYPES = ['C端业主', '设计师', '地产开发商', '酒店民宿业主', '家居行业经销商', '艺术家']
const PURPOSES = ['展厅参观', '全案设计咨询', '装修与建材选购', '家居产品选购', '商务合作', '其他']

const MIN_PARTY = 1
const MAX_PARTY = 50

function todayStr() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

Page({
  data: {
    visitorTypes: VISITOR_TYPES,
    purposes: PURPOSES,
    today: todayStr(),
    spaceId: '',
    spaceName: '',
    submitting: false,
    form: {
      name: '',
      phone: '',
      visitorTypeIndex: -1,
      visitDate: '',
      partySize: 2,
      purposeIndex: -1,
      note: ''
    }
  },

  // 从展厅卡片点进来时带上是哪一间，直接进表单则为空
  onLoad(query) {
    const spaceId = query.spaceId || ''
    const spaceName = query.spaceName ? decodeURIComponent(query.spaceName) : ''
    this.setData({ spaceId, spaceName })

    // 从某一间点进来的，预约需求默认「展厅参观」
    if (spaceId) {
      this.setData({ 'form.purposeIndex': PURPOSES.indexOf('展厅参观') })
    }
  },

  onNameInput(e) {
    this.setData({ 'form.name': e.detail.value })
  },

  onPhoneInput(e) {
    this.setData({ 'form.phone': e.detail.value })
  },

  onVisitorTypeChange(e) {
    this.setData({ 'form.visitorTypeIndex': Number(e.detail.value) })
  },

  onDateChange(e) {
    this.setData({ 'form.visitDate': e.detail.value })
  },

  onPartyStep(e) {
    const step = Number(e.currentTarget.dataset.step)
    const next = this.data.form.partySize + step
    if (next < MIN_PARTY || next > MAX_PARTY) return
    this.setData({ 'form.partySize': next })
  },

  onPurposeChange(e) {
    this.setData({ 'form.purposeIndex': Number(e.detail.value) })
  },

  onNoteInput(e) {
    this.setData({ 'form.note': e.detail.value })
  },

  // 与服务端同一套规则，先在本地拦一道，少一次网络往返
  buildPayload() {
    const f = this.data.form
    const name = f.name.trim()
    const phone = f.phone.trim()

    if (!name) return { error: '请填写您的称呼' }
    if (!phone) return { error: '请填写您的电话' }
    if (!/^1[3-9]\d{9}$/.test(phone)) return { error: '电话格式不正确' }
    if (f.visitorTypeIndex < 0) return { error: '请选择来者身份' }
    if (!f.visitDate) return { error: '请选择到访日期' }
    if (f.purposeIndex < 0) return { error: '请选择预约需求' }

    return {
      payload: {
        name,
        phone,
        visitorType: VISITOR_TYPES[f.visitorTypeIndex],
        visitDate: f.visitDate,
        partySize: f.partySize,
        purpose: PURPOSES[f.purposeIndex],
        note: f.note.trim(),
        spaceId: this.data.spaceId
      }
    }
  },

  onSubmit() {
    if (this.data.submitting) return

    const { error, payload } = this.buildPayload()
    if (error) {
      wx.showToast({ title: error, icon: 'none' })
      return
    }

    this.setData({ submitting: true })
    wx.showLoading({ title: '提交中', mask: true })

    // 带上登录态，服务端就能认出是谁提交的，「我的」页才拉得到这条；
    // 登录出问题也不挡着提交——这张表未登录同样收
    api
      .requestOptionalAuth({ url: '/api/appointments', method: 'POST', data: payload })
      .then(res => {
        if (res.statusCode === 201 && res.data && res.data.ok) {
          // 本机也存一份：万一这次没登录态，至少用户自己看得见刚提交的
          appointments.save({
            visitDate: payload.visitDate,
            partySize: payload.partySize,
            purpose: payload.purpose,
            visitorType: payload.visitorType,
            spaceName: this.data.spaceName,
            status: 'new'
          })

          wx.showModal({
            title: '已收到您的预约',
            content: `${payload.visitDate} · ${payload.partySize} 人\n我们会电话与您确认到访时间`,
            showCancel: false,
            confirmText: '好的',
            success: () => wx.navigateBack()
          })
        } else {
          const message = (res.data && res.data.message) || '提交失败，请稍后再试'
          wx.showToast({ title: message, icon: 'none', duration: 2600 })
        }
      })
      .catch(err => {
        wx.showToast({
          title: (err && err.message) || '网络异常，请检查后重试',
          icon: 'none',
          duration: 2600
        })
      })
      .then(() => {
        wx.hideLoading()
        this.setData({ submitting: false })
      })
  }
})
