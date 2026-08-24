// 瀑布流卡片的**外壳**，只提供白底 + 12rpx 圆角 + 溢出裁切 + 点按反馈。
//
// 它不知道自己装的是案例、好物还是别的什么——三种业务卡片的字段完全不同，
// 硬做成一个组件必然满是 wx:if。壳归壳，内容由页面自己拼。
Component({
  options: { multipleSlots: false },
  methods: {
    onTap() { this.triggerEvent('tap') }
  }
})
