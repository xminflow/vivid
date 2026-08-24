/**
 * 1299900 → 「12,999.00」
 *
 * 金额在整条链路上都是**整数分**：库里是 int、接口传的是 int、和微信支付对账的
 * 也是分。只有显示给人看的这一刻才换算成元，而且是拿整数做除法再补零，
 * 不走浮点——`1299900 / 100` 看着没事，但金额一旦参与加减就会攒出对账差。
 */
export function formatYuan(cents: number): string {
  const negative = cents < 0
  const abs = Math.abs(cents)
  const yuan = Math.floor(abs / 100)
  const fen = String(abs % 100).padStart(2, '0')
  return `${negative ? '-' : ''}${yuan.toLocaleString('zh-CN')}.${fen}`
}

/** 2026-08-05T14:03:22+08:00 → 2026-08-05 14:03 */
export function formatTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
