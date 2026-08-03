// 我的：会员资料 / 专属顾问 / 服务 / 预约
const profileStore = require('../../utils/profile.js')
const appointments = require('../../utils/appointments.js')
const { advisor, genders } = require('../../mock/mine.js')
const { brand } = require('../../mock/home.js')

const BIRTHDAY_START = '1930-01-01'

function todayStr() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function formatDate(s) {
  const parts = String(s || '').split('-')
  return parts.length === 3 ? parts.join('.') : s
}

// region picker 给的是 [省, 市, 区]。直辖市的省市同名，只显示一次
function formatRegion(region) {
  if (!Array.isArray(region) || !region.length) return ''
  const [province, city] = region
  if (!city || province === city) return province
  return `${province} ${city}`
}

Page({
  data: {
    brand,
    advisor,
    genders,
    profile: profileStore.EMPTY,
    regionText: '',
    birthdayStart: BIRTHDAY_START,
    today: todayStr(),
    records: []
  },

  // 预约是在别的页面提交的，每次回到这页都重读一次
  onShow() {
    const profile = profileStore.read()
    const records = appointments.list().map(r => ({
      ...r,
      dateText: formatDate(r.visitDate)
    }))

    this.setData({
      profile,
      regionText: formatRegion(profile.region),
      records
    })
  },

  // ---------- 我的信息 ----------

  // 每次改动都落盘。资料对象很小，不值得再搭一层防抖
  saveField(key, value) {
    const profile = { ...this.data.profile, [key]: value }
    profileStore.write(profile)
    this.setData({ profile })
    return profile
  },

  onMemberNameInput(e) {
    this.saveField('memberName', e.detail.value)
  },

  onPhoneInput(e) {
    this.saveField('phone', e.detail.value)
  },

  onEmailInput(e) {
    this.saveField('email', e.detail.value)
  },

  onBirthdayChange(e) {
    this.saveField('birthday', e.detail.value)
  },

  onGenderChange(e) {
    this.saveField('genderIndex', Number(e.detail.value))
  },

  onRegionChange(e) {
    const region = e.detail.value
    this.saveField('region', region)
    this.setData({ regionText: formatRegion(region) })
  },

  // ---------- 我的顾问 ----------

  onCallAdvisor() {
    wx.makePhoneCall({
      phoneNumber: advisor.phone,
      fail: () => {} // 用户自己取消拨号，不用提示
    })
  },

  onShareAppMessage() {
    return {
      title: 'ANTONY CASA 杭州展厅',
      path: '/pages/index/index'
    }
  }
})
