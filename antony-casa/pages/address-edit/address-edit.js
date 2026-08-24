// 新增 / 编辑收货地址。带 id 就是编辑，不带就是新增。
//
// 省市区用 picker 的 region 模式：微信自带三级联动，数据由微信维护，
// 不用我们自己塞一份行政区划表进包（那份表几十 KB，还会过期）。
//
// 校验放两处：这里做即时提示（哪一项没填、手机号格式），服务端再校一次。
// 前端这一层是为了不让用户点了提交才知道错，不是为了替代服务端——
// 服务端那一层才是真的防线（模型 + 库上的 CHECK）。
const api = require('../../utils/api.js')

// 与服务端 models.ShopAddressIn、schema.sql 的 CHECK 同一条正则
const PHONE = /^1[3-9]\d{9}$/

Page({
  data: {
    id: '',
    receiver: '',
    phone: '',
    // picker region 模式的值就是 [省, 市, 区]
    region: [],
    regionText: '',
    detail: '',
    isDefault: false,
    // 编辑已有的默认地址时，开关要禁掉：关掉它会让这个人一个默认地址都没有，
    // 而「取消默认」这个动作没有意义——要换默认地址是去另一条上点「设为默认」
    lockDefault: false,
    submitting: false
  },

  onLoad(query) {
    const id = (query && query.id) || ''
    if (!id) {
      wx.setNavigationBarTitle({ title: '新增收货地址' })
      return
    }
    this.setData({ id })
    wx.setNavigationBarTitle({ title: '编辑收货地址' })
    this.load(id)
  },

  /** 列表接口一把返回全部地址，从里面挑出这一条即可，不用单独的详情接口。
   * 地址簿本来就只有几条，为编辑页再加一条 GET /addresses/{id} 不划算。 */
  load(id) {
    api
      .request({ url: '/api/shop/addresses' })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '加载失败')
        }
        const item = res.data.items.find(row => row.id === id)
        if (!item) throw new Error('这条地址不存在')
        const region = [item.province, item.city, item.district]
        this.setData({
          receiver: item.receiver,
          phone: item.phone,
          region,
          regionText: region.join(' '),
          detail: item.detail,
          isDefault: item.isDefault,
          lockDefault: item.isDefault
        })
      })
      .catch(err => {
        console.error('加载地址失败', err)
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
      })
  },

  onInput(e) {
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value })
  },

  onRegion(e) {
    this.setData({ region: e.detail.value, regionText: e.detail.value.join(' ') })
  },

  onDefault(e) {
    if (this.data.lockDefault) return
    this.setData({ isDefault: e.detail.value })
  },

  /** 返回第一条错误信息，全部通过返回空串。 */
  validate() {
    const { receiver, phone, region, detail } = this.data
    if (!receiver.trim()) return '请填写收货人'
    if (!PHONE.test(phone.trim())) return '手机号格式不正确'
    if (region.length !== 3) return '请选择所在地区'
    if (!detail.trim()) return '请填写详细地址'
    if (detail.trim().length > 100) return '详细地址最多 100 个字'
    return ''
  },

  onSubmit() {
    if (this.data.submitting) return
    const error = this.validate()
    if (error) {
      wx.showToast({ title: error, icon: 'none' })
      return
    }

    const { id, receiver, phone, region, detail, isDefault } = this.data
    const body = {
      receiver: receiver.trim(),
      phone: phone.trim(),
      province: region[0],
      city: region[1],
      district: region[2],
      detail: detail.trim(),
      isDefault
    }

    this.setData({ submitting: true })
    api
      .request({
        url: id ? `/api/shop/addresses/${id}` : '/api/shop/addresses',
        method: id ? 'PUT' : 'POST',
        data: body
      })
      .then(res => {
        if (res.statusCode !== 200 || !res.data || !res.data.ok) {
          throw new Error((res.data && res.data.message) || '保存失败')
        }
        wx.showToast({ title: '已保存', icon: 'none' })
        // 等提示看得见再退，直接退会闪一下什么都看不到
        setTimeout(() => wx.navigateBack(), 600)
      })
      .catch(err => {
        console.error('保存地址失败', err)
        wx.showToast({ title: err.message || '保存失败', icon: 'none' })
        this.setData({ submitting: false })
      })
  }
})
