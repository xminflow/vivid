// 展示用的格式化。

// 浏览量。它是首页唯一外露的互动数字，一律降级为弱灰小字——不是给用户比较的分数。
//
// ⚠️ 一期它**不参与排序**（首页按发布时间倒序，见 CONTEXT.md「浏览量」），
// 这与需求文档 AJ-02 不一致，是有意偏离
function views(n) {
  if (n < 10000) return `${n} 浏览`
  return `${(n / 10000).toFixed(1).replace(/\.0$/, '')}万 浏览`
}

// 发布日期。只到天：案例不是时效性内容，精确到分秒既没用又让人下意识去比新旧。
// 当年的省掉年份——一屏里全是同一个年份，那四个字符不传递任何信息
function date(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const md = `${d.getMonth() + 1}月${d.getDate()}日`
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}年${md}`
}

module.exports = { views, date }
