// 发布案例。
//
// 图是**选完就传**，不是提交时一起传：微信端一次推九张原图必然超时，逐张传是这里
// 唯一可用的形态。代价是中途退出的草稿图会留在桶里成为孤儿——不做实时清理，按
// static/anjia-case/ 前缀由运维对账，与首页配图的取舍一致（见 server/app/admin.py）。
//
// 提交进的是人工审核队列，不是直接上首页。这一点必须在这一页说清楚，否则作者
// 发完找不到自己的内容，只会以为是坏了。

const api = require('../../utils/api.js')

const MAX_IMAGES = 9
const MAX_TITLE = 30
const MAX_BODY = 1000

Page({
  data: {
    // 已传好的图：{ key, url }。key 是提交时真正要给服务端的东西，
    // url 只用来预览——宽高焊在 key 里，客户端不经手（见 utils/api.js）
    images: [],
    title: '',
    body: '',
    uploading: 0,
    submitting: false,
    maxImages: MAX_IMAGES,
    maxTitle: MAX_TITLE,
    maxBody: MAX_BODY
  },

  onChooseImage() {
    const remain = MAX_IMAGES - this.data.images.length
    if (remain <= 0) return

    wx.chooseMedia({
      count: remain,
      mediaType: ['image'],
      // 压缩后再传：服务端要中转这些字节，原图九张能有几十兆。
      // 压缩顺带把 iPhone 的 heic 转成 jpg——服务端只收 jpg/png/webp
      sizeType: ['compressed'],
      success: res => this.upload(res.tempFiles.map(f => f.tempFilePath)),
      fail: () => {}
    })
  },

  // 逐张传。一张失败不连累其余：已经传上去的留着，失败的那张告诉他重选
  upload(paths) {
    this.setData({ uploading: this.data.uploading + paths.length })

    paths.forEach(path => {
      api
        .uploadCaseImage(path)
        .then(img => {
          this.setData({
            images: this.data.images.concat({ key: img.key, url: img.url }),
            uploading: this.data.uploading - 1
          })
        })
        .catch(err => {
          console.error('[publish] 上传失败', err)
          this.setData({ uploading: this.data.uploading - 1 })
          wx.showToast({ title: err.message || '图片上传失败', icon: 'none' })
        })
    })
  },

  onRemoveImage(e) {
    const index = e.currentTarget.dataset.index
    const images = this.data.images.slice()
    images.splice(index, 1)
    // 只从这条草稿里去掉，不删桶里的对象：删了之后撤销就没得撤了，
    // 而没被引用的对象本来就走前缀对账
    this.setData({ images })
  },

  onPreviewImage(e) {
    const urls = this.data.images.map(img => img.url).filter(Boolean)
    if (!urls.length) return
    wx.previewImage({ current: urls[e.currentTarget.dataset.index], urls })
  },

  onTitle(e) {
    this.setData({ title: e.detail.value })
  },

  onBody(e) {
    this.setData({ body: e.detail.value })
  },

  onSubmit() {
    const title = this.data.title.trim()
    if (!title) {
      wx.showToast({ title: '请填写标题', icon: 'none' })
      return
    }
    if (!this.data.images.length) {
      wx.showToast({ title: '至少上传一张图片', icon: 'none' })
      return
    }
    if (this.data.uploading > 0) {
      wx.showToast({ title: '还有图片在上传中', icon: 'none' })
      return
    }

    this.setData({ submitting: true })
    api
      .createCase({
        title,
        body: this.data.body.trim(),
        images: this.data.images.map(img => img.key)
      })
      .then(() => {
        // 用 showModal 而不是 toast：「要等审核」是这一步最要紧的信息，
        // 一个 1.5 秒就消失的提示传达不了它
        wx.showModal({
          title: '已提交',
          content: '案例已进入人工审核，通过后会出现在首页内容流。',
          showCancel: false,
          confirmText: '知道了',
          complete: () => wx.navigateBack()
        })
      })
      .catch(err => {
        console.error('[publish] 发布失败', err)
        this.setData({ submitting: false })
        wx.showToast({ title: err.message || '发布失败，请稍后再试', icon: 'none' })
      })
  }
})
