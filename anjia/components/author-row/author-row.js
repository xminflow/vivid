// 作者行：头像 + 名字 + 认证角标 + 一段弱信息（时间或计数）。
//
// 认证角标只表示「这个账号有一条生效的企业认证」。会员身份**不在这里出现**，
// 也不在任何对外可见的地方出现——会员是付费门槛不是荣誉体系，它唯一的权益是
// 发起私信，转化点应该落在「联系 TA」被拦下的那一刻，不在信息流里。
// 理由与被否掉的其他做法见 docs/adr/0003-会员身份对外不可见.md——想在这里加一个
// member 入参之前，先读那份。
Component({
  properties: {
    avatar: { type: String, value: '' },
    name: { type: String, value: '' },
    certified: { type: Boolean, value: false },
    // 右侧弱信息：时间、浏览量等。传空则不占位
    meta: { type: String, value: '' },
    // sm 用于瀑布流卡片内，md 用于详情页与账号主页
    size: { type: String, value: 'sm' }
  }
})
