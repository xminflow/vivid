// 标签。直角小方块（4rpx），不是胶囊——胶囊是消费类 App 的语汇。
Component({
  properties: {
    text: { type: String, value: '' },
    // default：淡底灰字，用于分类等中性信息
    // brand：赭底白字，只用在需要被一眼看到的极少数标签上，一屏别超过一个
    tone: { type: String, value: 'default' }
  }
})
