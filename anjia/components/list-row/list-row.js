// 单列条目的外壳。随安而遇的资源帖是纯文字，不该被塞进瀑布流——
// 文字条目在双列里会被压成两条窄柱，读起来很累。
Component({
  properties: {
    // 末条传 false，避免最后一条下面挂一根没有下文的线
    divided: { type: Boolean, value: true }
  },
  methods: {
    onTap() { this.triggerEvent('tap') }
  }
})
