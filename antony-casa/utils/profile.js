// 会员资料。
//
// 跟 appointments.js 同样的处境：还没有 openId，资料只能先存在本机。
// 服务端补上按 openId 读写资料的接口后，把 read/write 换成请求即可，页面不用动。

const KEY = 'memberProfile'

const EMPTY = {
  memberName: '',
  phone: '',
  email: '',
  birthday: '',
  genderIndex: -1,
  region: [] // [省, 市, 区]，用 picker mode="region" 选
}

function read() {
  try {
    const saved = wx.getStorageSync(KEY)
    return { ...EMPTY, ...(saved && typeof saved === 'object' ? saved : {}) }
  } catch (e) {
    return { ...EMPTY }
  }
}

function write(profile) {
  try {
    wx.setStorageSync(KEY, profile)
  } catch (e) {
    // 存不进去就算了，页面上的值还在，不打断用户填写
  }
  return profile
}

module.exports = { read, write, EMPTY }
