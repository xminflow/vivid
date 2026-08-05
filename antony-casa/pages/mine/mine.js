// 我的：会员资料 / 专属顾问 / 服务 / 预约
const profileStore = require('../../utils/profile.js')
const appointments = require('../../utils/appointments.js')
const { uploadImage } = require('../../utils/upload.js')
const { advisor, genders } = require('../../mock/mine.js')
const { brand, spaces } = require('../../mock/home.js')

const BIRTHDAY_START = '1930-01-01'

// 库里存的是状态码，页面要给人话
const STATE_TEXT = {
  new: '待确认',
  confirmed: '已确认',
  visited: '已到访',
  cancelled: '已取消'
}

// 服务端只存 spaceId，展厅名字在前端
function spaceName(spaceId) {
  const space = spaces.find(s => s.id === spaceId)
  return space ? space.name : ''
}

function todayStr() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function formatDate(s) {
  const parts = String(s || '').split('-')
  return parts.length === 3 ? parts.join('.') : s
}

// 顶部只露一个号码，中间四位打码
function maskPhone(phone) {
  return /^\d{11}$/.test(phone || '') ? `${phone.slice(0, 3)}****${phone.slice(7)}` : ''
}

// 取首字做头像。用 Array.from 而不是 slice(0,1)：名字以 emoji 开头时
// slice 会把代理对劈成半个字符，渲染出乱码
function initialOf(name) {
  const chars = Array.from(String(name || '').trim())
  return chars.length ? chars[0] : ''
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
    initial: '',
    phoneMasked: '',
    // 头像地址是有时效的，拉不出来时先退回首字，见 onAvatarError
    avatarBroken: false,
    // 图真加载出来了才让它显形，否则真机会先闪一帧系统裂图
    avatarReady: false,
    birthdayStart: BIRTHDAY_START,
    today: todayStr(),
    records: [],

    // 四段默认全收起。页面一眼看完有哪些东西，要哪段点哪段
    open: {
      info: false,
      advisor: false,
      service: false,
      appt: false
    }
  },

  // 预约是在别的页面提交的，每次回到这页都重读一次
  onShow() {
    this.editing = false

    // 先用本机快照渲染，别让用户盯着空白等网络
    this.renderProfile(profileStore.read())
    this.renderRecords(appointments.list())

    // 再拿服务端那份盖上去。用户已经动手填了就不盖了，免得把输入抹掉
    profileStore.fetch().then(profile => {
      if (!this.editing) this.renderProfile(profile)
    })
    appointments.fetchMine().then(items => {
      if (items) this.renderRecords(items)
    })
  },

  // 填了一半就切走时，把还没发出去的改动补上
  onHide() {
    profileStore.flush()
  },

  onUnload() {
    profileStore.flush()
  },

  renderProfile(profile) {
    const patch = {
      profile,
      regionText: formatRegion(profile.region),
      initial: initialOf(profile.memberName),
      phoneMasked: maskPhone(profile.phone)
    }
    // 换了地址才给它一次新机会。地址没变就别重置：这个方法每敲一个字都会走一次，
    // 否则会反复去重拉那张已经拉不出来的图
    if (profile.avatarUrl !== this.data.profile.avatarUrl) {
      patch.avatarBroken = false
      patch.avatarReady = false
    }
    this.setData(patch)
  },

  renderRecords(rows) {
    this.setData({
      records: rows.map(r => ({
        ...r,
        dateText: formatDate(r.visitDate),
        spaceName: r.spaceName || spaceName(r.spaceId),
        stateText: STATE_TEXT[r.status] || '待确认'
      }))
    })
  },

  // ---------- 头像 ----------

  // 微信在 2022-10-25 之后收回了 getUserProfile 的头像昵称，现在只剩 chooseAvatar
  // 这一条路：用户点一下，由微信弹窗让他选微信头像或自己的图片。
  // 拿到的是本机临时路径（wxfile://），小程序一重启就失效，必须立刻传到 COS，
  // 库里存对象键，显示用的地址由服务端现签。
  async onChooseAvatar(e) {
    const tempPath = e.detail.avatarUrl
    if (!tempPath) return

    const previous = this.data.profile.avatarUrl

    // 先把临时图顶上去，点完立刻看到换了，不用等网络
    this.setData({ 'profile.avatarUrl': tempPath, avatarBroken: false, avatarReady: false })
    wx.showLoading({ title: '保存中', mask: true })

    try {
      const key = await uploadImage(tempPath, 'avatar')
      const saved = await profileStore.saveAvatar(key)
      // 换成服务端签的正式地址：临时路径下次进页面就没了
      this.renderProfile({ ...this.data.profile, avatarUrl: saved.avatarUrl })
    } catch (err) {
      // 没存上就退回原来那张。留着临时图会让用户以为已经换好了，下次进来又变回去
      this.setData({ 'profile.avatarUrl': previous, avatarReady: false })
      profileStore.cacheAvatar(previous)
      wx.showToast({ title: err.message || '头像保存失败', icon: 'none', duration: 2400 })
    } finally {
      wx.hideLoading()
    }
  },

  // 头像地址是服务端签的临时地址，本机快照里那份放到下次冷启动可能已经过期。
  // 拉不出来就先退回首字，onShow 里的 fetch 会带回新地址
  onAvatarError() {
    this.setData({ avatarBroken: true, avatarReady: false })
  },

  // 图确实解出来了才盖到首字圆上面去
  onAvatarLoad() {
    this.setData({ avatarReady: true })
  },

  // 头像 button 上的 catchtap 要挂个方法，只为拦冒泡，本身不做事
  noop() {},

  // ---------- 折叠 ----------

  // 各段互不影响，不做「一次只开一个」：改资料的同时想核对预约是常见操作，
  // 互斥的话要来回点两次
  onToggleSection(e) {
    const key = e.currentTarget.dataset.k
    this.setData({ [`open.${key}`]: !this.data.open[key] })
  },

  // ---------- 我的信息 ----------

  // 本机立刻落盘，上传由 profile.js 防抖后发，打字过程中不会一直请求
  saveField(key, value) {
    this.editing = true
    const profile = { ...this.data.profile, [key]: value }
    profileStore.save(profile)
    // 走 renderProfile 而不是直接 setData：顶部的头像首字和打码号码
    // 是从 profile 派生的，改名改号时要跟着变
    this.renderProfile(profile)
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
    // regionText 由 renderProfile 一并算出，这里不用再单独设一次
    this.saveField('region', e.detail.value)
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
