// 会员资料。
//
// 真身在服务端（users 表，按 openid 认人），本机存一份快照：
// 进「我的」页先用快照渲染，不让用户盯着空白等网络。
//
// 页面是改一个字段就调一次 save，所以这里防抖 SAVE_DELAY 再发请求——
// 否则用户打十个字就是十次 PUT。离开页面时页面会调 flush 把没发的补上。

const api = require('./api.js')
const { genders } = require('../mock/mine.js')

const KEY = 'memberProfile'
const SAVE_DELAY = 800

const EMPTY = {
  memberName: '',
  phone: '',
  email: '',
  birthday: '',
  genderIndex: -1,
  region: [] // [省, 市, 区]，用 picker mode="region" 选
}

let saveTimer = null
let pending = null

function read() {
  try {
    const saved = wx.getStorageSync(KEY)
    return { ...EMPTY, ...(saved && typeof saved === 'object' ? saved : {}) }
  } catch (e) {
    return { ...EMPTY }
  }
}

function writeCache(profile) {
  try {
    wx.setStorageSync(KEY, profile)
  } catch (e) {
    // 存不进去就算了，页面上的值还在，不打断用户填写
  }
  return profile
}

// 库里存的是性别文字，页面用的是下标。存下标会在选项顺序一改时全错位
function fromServer(user) {
  const index = genders.indexOf(user.gender)
  return {
    memberName: user.memberName || '',
    phone: user.phone || '',
    email: user.email || '',
    birthday: user.birthday || '',
    genderIndex: index,
    region: Array.isArray(user.region) ? user.region : []
  }
}

function toServer(profile) {
  return {
    memberName: profile.memberName,
    phone: profile.phone,
    email: profile.email,
    birthday: profile.birthday || null,
    gender: genders[profile.genderIndex] || '',
    region: profile.region || []
  }
}

// 从服务端取一份最新的，顺手更新快照。失败就还用快照，不打扰用户
function fetch() {
  return api
    .request({ url: '/api/users/me' })
    .then(res => {
      if (res.statusCode !== 200 || !res.data.ok) throw new Error(res.data.message || '')
      // 本机还有没上传的改动，那本机才是新的，别被这次的回包盖掉
      return pending ? read() : writeCache(fromServer(res.data.user))
    })
    .catch(() => read())
}

function push(profile) {
  return api.request({ url: '/api/users/me', method: 'PUT', data: toServer(profile) })
}

// 先落本机再排队上传：网络慢也不影响页面上的值
function save(profile) {
  writeCache(profile)
  pending = profile
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(flush, SAVE_DELAY)
  return profile
}

// 立刻把待写的发出去。页面 onHide / onUnload 时调，免得填完就退出丢掉最后一次改动
function flush() {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  if (!pending) return Promise.resolve(null)

  const profile = pending
  pending = null
  return push(profile).catch(() => {
    // 没传上去不提示：用户可能已经离开这页了。本机还留着，下次 save 会再带上
  })
}

module.exports = { read, save, flush, fetch, EMPTY }
