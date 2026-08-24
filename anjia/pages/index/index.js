// 首页内容流。案例的真实列表。
//
// 「关注」那个 tab 摘掉了：关注、粉丝数、账号主页（需求文档 AJ-03）是独立一刀，
// 留着一个永远是空的 tab，比没有这个 tab 更像 bug。
//
// 一期按**发布时间**倒序，不按浏览量——这与需求文档 AJ-02 不一致，是有意偏离，
// 理由写在 server/app/anjia/cases.py 的 list_cases 上。

const api = require('../../utils/api.js')
const { pack } = require('../../utils/waterfall.js')
const { views } = require('../../utils/format.js')

// 一条案例在瀑布流里用得到的那几项。
//
// w / h 取自封面，服务端在上传时读文件头算好的：瀑布流必须在图片加载**之前**
// 就知道卡片多高，否则页面会先塌成空白再被内容顶开。
function card(item) {
  const cover = item.cover || { url: '', w: 3, h: 4 }
  return {
    id: item.id,
    title: item.title,
    image: cover.url,
    w: cover.w,
    h: cover.h,
    author: { name: item.author.name, avatar: item.author.avatarUrl },
    viewsText: views(item.views)
  }
}

Page({
  data: {
    loading: true,
    // 骨架的比例刻意错开，让加载态看起来就是参差的瀑布流，而不是一排等高方块
    skeletons: [[1.5, 0.66, 1.33], [0.75, 1.5, 1.1]],
    cols: [[], []],
    error: '',
    loadingMore: false,
    noMore: false,
    // 个人账号也看得见发布按钮，点了引导去认证——认证是这个小程序的关键转化漏斗，
    // 把入口藏起来，绝大多数用户永远不会知道认证能换来什么
    canPublish: false
  },

  // 已加载的全部案例。翻页时要拿整份重新装箱，所以留在这里而不是 data 里：
  // 它不参与渲染，放 data 只会让每次 setData 多搬一份数据过去
  items: [],
  cursor: '',

  onLoad() {
    this.load()
  },

  onShow() {
    // 从认证页回来时身份可能已经变了，重新问一次。失败不影响看内容，
    // 所以只把按钮退回「不能发」，不弹错
    api
      .me()
      .then(user => this.setData({ canPublish: user.accountType === 'company' }))
      .catch(() => this.setData({ canPublish: false }))
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    this.loadMore()
  },

  load(done) {
    this.setData({ loading: true, error: '', cols: [[], []], noMore: false })
    api
      .listCases('')
      .then(body => {
        this.items = body.items
        this.cursor = body.nextCursor || ''
        this.setData({
          loading: false,
          noMore: !body.nextCursor,
          cols: pack(this.items.map(card))
        })
      })
      .catch(err => {
        // 不静默吞掉：这一页失败了就只剩一片空白，必须说清是哪一步没通
        console.error('[index] 加载内容流失败', err)
        this.setData({ loading: false, error: err.message || '加载失败，请稍后再试' })
      })
      .then(() => done && done())
  },

  loadMore() {
    if (this.data.loading || this.data.loadingMore || this.data.noMore) return

    this.setData({ loadingMore: true })
    api
      .listCases(this.cursor)
      .then(body => {
        this.items = this.items.concat(body.items)
        this.cursor = body.nextCursor || ''
        // 拿**整份**重新装箱，不是往两列尾部各追加一段：贪心装箱要知道两列当前
        // 各有多高才能决定下一张放哪，只追加会让新来的这一批自己内部平衡、
        // 与已有的两列错开。列表是几十条的量级，重排的代价可以忽略
        this.setData({
          loadingMore: false,
          noMore: !body.nextCursor,
          cols: pack(this.items.map(card))
        })
      })
      .catch(err => {
        console.error('[index] 加载更多失败', err)
        this.setData({ loadingMore: false })
        wx.showToast({ title: err.message || '加载失败', icon: 'none' })
      })
  },

  onCase(e) {
    wx.navigateTo({ url: `/pages/case/case?id=${e.currentTarget.dataset.id}` })
  },

  onPublish() {
    if (this.data.canPublish) {
      wx.navigateTo({ url: '/pages/publish/publish' })
      return
    }
    // 这个弹层是企业认证唯一的自然获客点：它出现在用户真的想发东西的那一刻
    wx.showModal({
      title: '仅企业认证账号可发布',
      content: '首页内容流只接受企业认证账号发布的案例。完成认证后即可发布。',
      confirmText: '去认证',
      success: res => {
        if (res.confirm) wx.navigateTo({ url: '/pages/certification/certification' })
      }
    })
  },

  onRetry() {
    this.load()
  }
})
