// 服务申请表单。字段全部由 mock/service.js 的 fields 数组驱动，这里不写死任何一项。
const { findService } = require('../../mock/service.js')
const { uploadImage } = require('../../utils/upload.js')
const { API_BASE } = require('../../utils/config.js')
const profileStore = require('../../utils/profile.js')

const MAX_PER_GROUP = 6

// 购买年份这类选项跟当前时间有关，写死在 mock 里会过期，进页面时现生成
function yearOptions() {
  const now = new Date().getFullYear()
  const years = []
  for (let y = now; y >= now - 20; y--) years.push(String(y))
  years.push('更早')
  return years
}

function regionText(region) {
  if (!Array.isArray(region) || !region.length) return ''
  const [province, city] = region
  if (!city || province === city) return province
  return `${province} ${city}`
}

Page({
  data: {
    service: null,
    rows: [],     // 渲染用的字段行，含当前值
    groups: [],   // 图片上传组
    submitting: false
  },

  onLoad(query) {
    const service = findService(query.id)
    if (!service) {
      wx.showToast({ title: '服务不存在', icon: 'none' })
      setTimeout(() => wx.navigateBack(), 1200)
      return
    }

    // 联系方式从「我的信息」带入，用户填过一次就不用再填
    const profile = profileStore.read()

    const rows = service.fields.map(f => {
      const options = f.type === 'select' && !f.options.length ? yearOptions() : f.options
      let value = ''
      if (f.id === 'name') value = profile.memberName || ''
      if (f.id === 'phone') value = profile.phone || ''
      return {
        ...f,
        options: options || [],
        value,          // text / phone / number / textarea 用
        index: -1,      // select 用
        region: [],     // region 用
        text: ''        // select / region 的显示文案
      }
    })

    const groups = service.uploads.map(u => ({ ...u, files: [] }))

    wx.setNavigationBarTitle({ title: service.name })
    this.setData({ service, rows, groups })
  },

  // ---------- 字段 ----------

  rowIndex(e) {
    return Number(e.currentTarget.dataset.i)
  },

  onInput(e) {
    this.setData({ [`rows[${this.rowIndex(e)}].value`]: e.detail.value })
  },

  onSelect(e) {
    const i = this.rowIndex(e)
    const index = Number(e.detail.value)
    this.setData({
      [`rows[${i}].index`]: index,
      [`rows[${i}].text`]: this.data.rows[i].options[index]
    })
  },

  onRegion(e) {
    const i = this.rowIndex(e)
    this.setData({
      [`rows[${i}].region`]: e.detail.value,
      [`rows[${i}].text`]: regionText(e.detail.value)
    })
  },

  // ---------- 图片 ----------

  // 选完就传，不攒到提交时才一起传：提交那一下卡十几秒体验很差，
  // 而且传到一半失败还得让用户重选
  onPickImage(e) {
    const gi = Number(e.currentTarget.dataset.g)
    const group = this.data.groups[gi]
    const room = MAX_PER_GROUP - group.files.length
    if (room <= 0) {
      wx.showToast({ title: `每组最多 ${MAX_PER_GROUP} 张`, icon: 'none' })
      return
    }

    wx.chooseMedia({
      count: room,
      mediaType: ['image'],
      sizeType: ['compressed'],
      success: res => this.uploadFiles(gi, res.tempFiles.map(f => f.tempFilePath))
    })
  },

  async uploadFiles(gi, paths) {
    const group = this.data.groups[gi]
    const scene = `${this.data.service.id}-${group.id}`

    // 先把占位塞进去，用户能立刻看到缩略图和「上传中」
    const start = group.files.length
    const pending = paths.map(path => ({ path, key: '', failed: false }))
    this.setData({ [`groups[${gi}].files`]: group.files.concat(pending) })

    for (let n = 0; n < paths.length; n++) {
      const at = start + n
      try {
        const key = await uploadImage(paths[n], scene)
        this.setData({ [`groups[${gi}].files[${at}].key`]: key })
      } catch (err) {
        this.setData({ [`groups[${gi}].files[${at}].failed`]: true })
        wx.showToast({ title: err.message || '图片上传失败', icon: 'none', duration: 2400 })
      }
    }
  },

  onRemoveImage(e) {
    const { g, i } = e.currentTarget.dataset
    const files = this.data.groups[g].files.slice()
    files.splice(Number(i), 1)
    this.setData({ [`groups[${g}].files`]: files })
  },

  onPreviewImage(e) {
    const { g, i } = e.currentTarget.dataset
    const urls = this.data.groups[g].files.map(f => f.path)
    wx.previewImage({ current: urls[Number(i)], urls })
  },

  // ---------- 提交 ----------

  buildPayload() {
    const fields = {}
    let name = ''
    let phone = ''

    for (const row of this.data.rows) {
      let value = ''
      if (row.type === 'select') value = row.index < 0 ? '' : row.options[row.index]
      else if (row.type === 'region') value = row.text
      else value = String(row.value || '').trim()

      if (row.required && !value) return { error: `请填写${row.label}` }

      if (row.id === 'name') name = value
      else if (row.id === 'phone') phone = value
      else if (value) fields[row.id] = value
    }

    if (!/^1[3-9]\d{9}$/.test(phone)) return { error: '联系方式格式不正确' }

    // 只提交传成功的图。传失败的留在页面上让用户自己删或重传
    const images = {}
    for (const group of this.data.groups) {
      const keys = group.files.filter(f => f.key).map(f => f.key)
      if (keys.length) images[group.id] = keys
    }

    const uploading = this.data.groups.some(g => g.files.some(f => !f.key && !f.failed))
    if (uploading) return { error: '图片还在上传，请稍候' }

    return { payload: { serviceId: this.data.service.id, name, phone, fields, images } }
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

    wx.request({
      url: `${API_BASE}/api/service-applications`,
      method: 'POST',
      header: { 'Content-Type': 'application/json' },
      data: payload,
      success: res => {
        if (res.statusCode === 201 && res.data && res.data.ok) {
          wx.showModal({
            title: '已收到您的申请',
            content: `${this.data.service.name}\n我们会尽快电话与您联系`,
            showCancel: false,
            confirmText: '好的',
            success: () => wx.navigateBack()
          })
        } else {
          const message = (res.data && res.data.message) || '提交失败，请稍后再试'
          wx.showToast({ title: message, icon: 'none', duration: 2600 })
        }
      },
      fail: () => {
        wx.showToast({ title: '网络异常，请检查后重试', icon: 'none', duration: 2600 })
      },
      complete: () => {
        wx.hideLoading()
        this.setData({ submitting: false })
      }
    })
  }
})
