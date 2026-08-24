// 空态。不画插画——一张插画会成为整页唯一的大色块，把「这里什么都没有」
// 这件事渲染得比内容还响。一行说明 + 一行出路就够。
Component({
  properties: {
    text: { type: String, value: '这里还没有内容' },
    hint: { type: String, value: '' },
    // 有 action 才出按钮。空态给不出下一步时，不要放一个假的按钮
    action: { type: String, value: '' }
  },
  methods: {
    onAction() { this.triggerEvent('action') }
  }
})
