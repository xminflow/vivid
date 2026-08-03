// 「我的」页里不属于用户自己的那部分内容。接口就绪后把导出换成请求结果即可。

// 专属服务顾问。
//
// name 需求方还没定人，先留空——页面会显示「（顾问姓名待定）」，定了直接填这里。
// qrcode 是从 code.jpg 里裁出来的微信个人码，只保留码本身（原图上下的标题和
//   「扫二维码，添加我为朋友」跟页面文案重复）。换人时把新码丢进 assets/advisor/
//   改这个路径即可；留空则页面显示占位框，不会出裂图。
const advisor = {
  name: '',
  phone: '18069816211',
  qrcode: '/assets/advisor/wechat-qr.png'
}

// 性别选项。「不便告知」是必须留的一档，不强迫用户二选一
const genders = ['女', '男', '不便告知']

module.exports = { advisor, genders }
