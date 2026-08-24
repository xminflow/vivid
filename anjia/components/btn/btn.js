// 按钮。直角矩形（8rpx），**不用胶囊**——胶囊 + 大圆角 + 投影是消费类 App 的
// 语汇，和「简约大气」是反向的。
//
// 三种：primary 赭底白字（一屏最多一个，它是这一屏要你做的那件事）；
// ghost 细描边（次要动作）；text 纯文字（第三级，弱到几乎不占视觉重量）。
Component({
  properties: {
    text: { type: String, value: '' },
    type: { type: String, value: 'primary' },
    size: { type: String, value: 'md' },
    disabled: { type: Boolean, value: false },
    block: { type: Boolean, value: false }
  },
  methods: {
    onTap() {
      if (this.data.disabled) return
      this.triggerEvent('tap')
    }
  }
})
