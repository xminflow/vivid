// 收货地址簿：列表、设默认、删除。新增与编辑在 pages/address-edit。
//
// 两种进入方式，靠 query 上的 pick 区分：
//   从「我的」进来   —— 纯管理，点某一条是去编辑
//   从结算页进来（pick=1）—— 选地址，点某一条是选中并返回，不进编辑
// 不做成两个页面：列表长得一模一样，复制一份迟早两边不一致。
//
// 选中怎么传回结算页：写进上一页的 data 再 navigateBack。用 getCurrentPages()
// 而不是 EventChannel——EventChannel 只在 navigateTo 时建立，而结算页是被
// navigateBack 回去的，那时通道已经没了。
const api = require('../../utils/api.js')

Page({
  data: {
    items: [],
    loading: true,
    // 选地址模式。true 时点一行是选中返回，不是进编辑
    picking: false,
    busyId: ''
  },

  onLoad(query) {
    this.setData({ picking: query && query.pick === '1' })
  },

  // 编辑完地址返回本页要看到最新的，所以放 onShow 不是 onLoad
  onShow() {
    this.load()
  },

  load() {
    this.setData({ loading: true })
    return api
      .request({ url: '/api/shop/addresses' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        this.setData({ items: res.data.items })
      })
      .catch(err => {
        console.error('地址簿加载失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ loading: false })
      })
  },

  onPick(e) {
    const id = e.currentTarget.dataset.id
    const item = this.data.items.find(row => row.id === id)
    if (!item) return

    if (!this.data.picking) {
      wx.navigateTo({ url: `/pages/address-edit/address-edit?id=${id}` })
      return
    }

    // 直接写上一页的 data。结算页只认这一个字段，拿到就重渲染
    const pages = getCurrentPages()
    const previous = pages[pages.length - 2]
    if (previous && previous.setData) {
      previous.setData({ address: item })
    }
    wx.navigateBack()
  },

  onEdit(e) {
    // 选地址模式下整行是「选中」，编辑要单独一个按钮，否则选不了也改不了
    wx.navigateTo({ url: `/pages/address-edit/address-edit?id=${e.currentTarget.dataset.id}` })
  },

  onSetDefault(e) {
    const id = e.currentTarget.dataset.id
    if (this.data.busyId) return
    this.setData({ busyId: id })
    api
      .request({ url: `/api/shop/addresses/${id}/default`, method: 'PUT' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '操作失败')
        }
        return this.load()
      })
      .catch(err => {
        console.error('设置默认地址失败', err)
        wx.showToast({ title: err.message || '操作失败', icon: 'none' })
      })
      .then(() => {
        this.setData({ busyId: '' })
      })
  },

  onRemove(e) {
    const id = e.currentTarget.dataset.id
    const item = this.data.items.find(row => row.id === id)
    if (!item) return

    wx.showModal({
      title: '删除地址',
      content: `确定删除「${item.receiver} ${item.detail}」？`,
      success: res => {
        if (!res.confirm) return
        api
          .request({ url: `/api/shop/addresses/${id}`, method: 'DELETE' })
          .then(r => {
            if (r.statusCode !== 200 || !r.data || !r.data.ok) {
              throw new Error((r.data && r.data.message) || '删除失败')
            }
            this.load()
          })
          .catch(err => {
            console.error('删除地址失败', err)
            wx.showToast({ title: err.message || '删除失败', icon: 'none' })
          })
      }
    })
  },

  onAdd() {
    wx.navigateTo({ url: '/pages/address-edit/address-edit' })
  }
})
