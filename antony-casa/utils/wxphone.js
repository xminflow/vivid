// 微信手机号快速验证：把 <button open-type="getPhoneNumber"> 回调里的一次性 code
// 换成 11 位手机号。
//
// 换号本身在服务端做（POST /api/users/me/phone）——那个接口要 access_token，
// 而 access_token 是账号级密钥，绝不能下发到小程序里。
//
// ⚠️ 微信对这个接口按调用次数收费，服务端按人按天限了配额，超额回 429。
// 所以这里**不做任何自动重试**：重试一次就多花一次钱，而失败时让用户手输更快。

const api = require('./api.js')

// 从 code 换号。失败一律 reject，调用方自己决定提不提示
function resolvePhone(code) {
  return api
    .request({ url: '/api/users/me/phone', method: 'POST', data: { code } })
    .then(res => {
      if (res.statusCode !== 200 || !res.data.ok) {
        throw new Error((res.data && res.data.message) || '获取失败，请手动输入')
      }
      return res.data.phone
    })
}

module.exports = { resolvePhone }
