// 手机号空态：一个长得跟空输入框一样的 button，点下去弹微信授权。
//
// ## 为什么是「替换 input」而不是「盖一层」
//
// input 是**原生组件**（见 component/native-component.html）。官方原话：「原生组件的
// 层级是最高的，页面中其他组件无论 z-index 设多少都无法盖在原生组件上」。所以在
// input 上盖一层透明 button 去接管点击，在真机上必然失效——点击直接穿到 input，
// 授权永远调不起来。而开发者工具是用 web 组件模拟原生组件的，遮罩在工具里能吃到
// 点击，于是表现为「工具里像是好的、真机上完全没反应」。这一版踩过这个坑。
//
// 结论：空态这一格必须由 button 本体占着，页面那个 input 此时根本不渲染；
// 有值之后换回 input，直接编辑。
//
// ## 绕不开的约束
//
// getPhoneNumber 只能挂在 <button open-type="getPhoneNumber"> 上，且必须由用户
// **真实点击这个 button** 触发。没有任何 API 能从 input 的 focus/tap 回调里唤起它。
//
// ## 失败一律让路
//
// 拒绝授权、基础库过低、服务端换号失败——任何一种都立刻 triggerEvent('manual')，
// 页面据此换回 input 并聚焦。用户不会因为授权这条路不通就填不了表。
//
// ⚠️ 让路的判据只有「授权回调回来了」这一个，**不能再加超时兜底**。
// 曾经加过一个 1.5 秒的定时器，想兜住「回调永远不来」的情况，结果是：用户在授权
// 弹窗上停留超过 1.5 秒（这是常态），定时器就先触发了——输入框自己冒出来，
// 而且页面据此把 phone-get 从树上摘掉，组件实例销毁，用户随后点「允许」的回调
// 投递不到任何人，换号请求根本发不出去。超时兜底在这里是净损失。
//
// 去掉超时之后，页面用 wx:if 在 phone-get 和 input 之间切换就是安全的：切换只发生
// 在授权回调已经处理完之后（成功写了值、或失败转手输），不存在「组件还等着回调
// 却被销毁」的时机。（中间试过用 hidden 让两个节点都常驻，但自定义组件对 hidden
// 不生效——节点照样占位，占位文案还露在外面。）

const api = require('../../utils/api.js')
const { resolvePhone } = require('../../utils/wxphone.js')

Component({
  // 微信要求：用 getPhoneNumber 之前必须先调用过 wx.login。
  // app.js 的 onLaunch 已经静默登录了一次，但那次是 .catch(() => {}) 吞掉失败的——
  // 网络抖一下或者域名没配，登录就没成，而 getPhoneNumber 会表现为「点了没反应」，
  // 且不留任何痕迹。这里补一道：没有登录态就自己再登一次，login() 内部有并发去重
  attached() {
    if (!api.getToken()) {
      api.login().catch(err => console.warn('[phone-get] 预登录失败，授权可能调不起来', err))
    }
  },

  properties: {
    // 与页面那个 input 用同一句占位符，两种状态看起来才是同一格
    placeholder: {
      type: String,
      value: '11 位手机号'
    }
  },

  methods: {
    // 只要点到了就会进这里，与授权成不成功无关。
    // 这条日志能一刀切开两类故障：它不打 = 按钮没收到点击（布局问题）；
    // 它打了但后面没有「授权回调」= 微信没弹授权（平台侧条件不满足）
    onTap() {
      console.log('[phone-get] 点到空态按钮，等待微信授权回调')
    },

    onGetPhoneNumber(e) {
      const detail = e.detail || {}
      const errMsg = String(detail.errMsg || '')
      console.log('[phone-get] 授权回调', errMsg, detail)

      if (errMsg.indexOf(':ok') < 0) {
        // 原始 errMsg 一定要打出来：没开通「手机号快速验证」、主体不符、额度用尽，
        // 微信都只在这里给一句话，控制台之外没有第二个地方能看到
        console.warn('[phone-get] 未拿到授权', errMsg)
        this.toManual()

        // 用户自己点的「拒绝」不提示——他知道刚做了什么，再弹一句是在指责他。
        // 其余都是他没法理解也没法处理的失败，必须告诉他还能手输
        if (!/deny|cancel/i.test(errMsg)) {
          wx.showToast({ title: '获取失败，请手动输入', icon: 'none' })
        }
        return
      }

      // 新版给的是一次性 code。低于基础库 2.21.2 这里是空的，只有 encryptedData，
      // 本项目不做旧版解密，直接转手输
      if (!detail.code) {
        console.warn('[phone-get] 回调里没有 code，基础库可能低于 2.21.2')
        wx.showToast({ title: '获取失败，请手动输入', icon: 'none' })
        this.toManual()
        return
      }

      wx.showLoading({ title: '获取中', mask: true })
      resolvePhone(detail.code)
        .then(phone => {
          wx.hideLoading()
          console.log('[phone-get] 换号成功')
          // 页面把值写回去之后就有值了，这一格自然换成 input
          this.triggerEvent('got', { phone })
        })
        .catch(err => {
          wx.hideLoading()
          // 不吞：服务端已经把「超配额」「code 失效」翻成了能直接弹的中文
          console.error('[phone-get] 换手机号失败', err)
          wx.showToast({ title: err.message || '获取失败，请手动输入', icon: 'none' })
          this.toManual()
        })
    },

    // 让页面换回 input 并聚焦，用户接着就能打字
    toManual() {
      this.triggerEvent('manual')
    }
  }
})
