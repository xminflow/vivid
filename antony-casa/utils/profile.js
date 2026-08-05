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
  region: [], // [省, 市, 区]，用 picker mode="region" 选
  // 服务端现签的临时地址，不是固定 URL。空串表示没设过头像，页面显示会员名首字
  avatarUrl: ''
}

let saveTimer = null
let pending = null

// 只有 https 地址能跨会话复用（COS 现签的地址、微信授权带回的头像外链）。
// chooseAvatar 给的是本次会话的临时文件（http://tmp/... 或 wxfile://...），
// 进程一重启文件就没了，这种地址绝不能进快照
function isPersistentAvatar(url) {
  return /^https:\/\//.test(url || '')
}

function withoutTempAvatar(profile, where) {
  if (!profile.avatarUrl || isPersistentAvatar(profile.avatarUrl)) return profile
  console.debug('[profile] 丢弃不可跨会话的头像地址', where, profile.avatarUrl)
  return { ...profile, avatarUrl: '' }
}

function read() {
  try {
    const saved = wx.getStorageSync(KEY)
    // 老版本可能已经把临时路径写进去了，读的时候再挡一道
    return withoutTempAvatar({ ...EMPTY, ...(saved && typeof saved === 'object' ? saved : {}) }, 'read')
  } catch (e) {
    return { ...EMPTY }
  }
}

function writeCache(profile) {
  try {
    // 落盘的是去掉临时头像的版本，返回的仍是原件：
    // 上传期间页面要拿临时图做预览，只是这份预览不该被持久化
    wx.setStorageSync(KEY, withoutTempAvatar(profile, 'write'))
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
    region: Array.isArray(user.region) ? user.region : [],
    avatarUrl: user.avatarUrl || ''
  }
}

// 头像不在这里：它走自己的接口，PUT /api/users/me 只管「我的信息」那张表单
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

// 换头像。传的是图片直传 COS 后拿到的对象键，服务端存键、回签好的地址。
//
// 不走上面那套防抖队列：换头像是一次性动作而不是连续输入，用户点完就等着看结果，
// 失败必须马上告诉他，所以这里出错直接抛给页面。
function saveAvatar(key) {
  return api
    .request({ url: '/api/users/me/avatar', method: 'PUT', data: { avatarKey: key } })
    .then(res => {
      if (res.statusCode !== 200 || !res.data.ok) {
        throw new Error((res.data && res.data.message) || '头像保存失败，请稍后再试')
      }
      // 只把头像盖到快照上。用户可能正填着表单，整份覆盖会把还没上传的改动抹掉
      return writeCache({ ...read(), avatarUrl: res.data.user.avatarUrl || '' })
    })
}

// 只改本机快照里的头像，不发请求。
// 换头像失败要退回原来那张时用：期间用户若正好改了别的字段，快照里已经落进了
// 那个失效的临时路径，不擦掉的话下次冷启动第一眼就是张裂图
function cacheAvatar(avatarUrl) {
  return writeCache({ ...read(), avatarUrl: avatarUrl || '' })
}

module.exports = { read, save, flush, fetch, saveAvatar, cacheAvatar, EMPTY }
