// 预约参观登记表。选项值必须与 server/schema.sql 里的 CHECK 约束一致。
const api = require('../../utils/api.js')
const appointments = require('../../utils/appointments.js')
const profileStore = require('../../utils/profile.js')

const VISITOR_TYPES = ['业主', '设计师', '地产圈', '家居圈', '酒店民宿圈', '艺术圈']
const PURPOSES = ['展厅参观', '全案设计咨询', '装修建材订购', '家具软装选购', '商务合作', '其他']

const MIN_PARTY = 1
const MAX_PARTY = 50

// 到访时间的可选范围，与 mock/home.js 里展厅那条「周二至周日 10:00 - 19:00」对齐。
// 服务端不校验时段（营业时间是运营会改的，见 server/schema.sql 上的说明），
// 只有这一处在卡，改营业时间时这两个数要跟着 mock/home.js 一起改
const OPEN_TIME = '10:00'
const CLOSE_TIME = '19:00'

const pad = n => String(n).padStart(2, '0')

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function nowTimeStr() {
  const d = new Date()
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

Page({
  data: {
    visitorTypes: VISITOR_TYPES,
    purposes: PURPOSES,
    today: todayStr(),
    openTime: OPEN_TIME,
    closeTime: CLOSE_TIME,
    submitting: false,
    // 授权走不通时由 phone-get 通知：换回 input 并把光标落进去，他接着就能打字
    phoneManual: false,
    phoneFocus: false,
    form: {
      name: '',
      phone: '',
      visitorTypeIndex: -1,
      visitDate: '',
      visitTime: '',
      partySize: 2,
      // 这张表只有首页「立即预约」一个入口，来的都是要看展厅的，先替他选上
      purposeIndex: PURPOSES.indexOf('展厅参观'),
      note: ''
    }
  },

  // 手机号从「我的信息」带入，用户填过一次就不用再填一遍；带进来之后照样能改。
  // 读的是本机快照（同步、无网络），与服务申请表单同一套做法，见 pages/apply/apply.js。
  // 带进来就有值了，那一格直接是可编辑的 input，不再是「微信获取」按钮——
  // 号码已经在手上，没必要再花一次授权
  onLoad() {
    const phone = profileStore.read().phone || ''
    if (phone) this.setData({ 'form.phone': phone })
  },

  onNameInput(e) {
    this.setData({ 'form.name': e.detail.value })
  },

  onPhoneInput(e) {
    this.setData({ 'form.phone': e.detail.value })
  },

  // 「微信获取」拿回来的号，直接填进这张表。资料里的手机号由服务端在为空时补上，
  // 这里不用管，也不提示——用户要的是把表填上，不是被告知资料被改了
  onWxPhone(e) {
    this.setData({ 'form.phone': e.detail.phone })
  },

  onPhoneManual() {
    this.setData({ phoneManual: true, phoneFocus: true })
  },

  onVisitorTypeChange(e) {
    this.setData({ 'form.visitorTypeIndex': Number(e.detail.value) })
  },

  onDateChange(e) {
    this.setData({ 'form.visitDate': e.detail.value })
  },

  onTimeChange(e) {
    this.setData({ 'form.visitTime': e.detail.value })
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
    if (!f.visitTime) return { error: '请选择到访时间' }
    // 日期能选今天，时间列却是固定的营业时段，两个选择器各自合法、合起来
    // 可能是今天已经过去的钟点。'HH:MM' 定长补零，直接比字符串就是比时刻
    if (f.visitDate === todayStr() && f.visitTime <= nowTimeStr()) {
      return { error: '这个时间已经过了，换个时间或日期' }
    }
    if (f.purposeIndex < 0) return { error: '请选择预约需求' }

    return {
      payload: {
        name,
        phone,
        visitorType: VISITOR_TYPES[f.visitorTypeIndex],
        visitDate: f.visitDate,
        visitTime: f.visitTime,
        partySize: f.partySize,
        purpose: PURPOSES[f.purposeIndex],
        note: f.note.trim()
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
            visitTime: payload.visitTime,
            partySize: payload.partySize,
            purpose: payload.purpose,
            visitorType: payload.visitorType,
            status: 'new'
          })

          wx.showModal({
            title: '已收到您的预约',
            content: `${payload.visitDate} ${payload.visitTime} · ${payload.partySize} 人\n我们会电话与您确认到访安排`,
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
