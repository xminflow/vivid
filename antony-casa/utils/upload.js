// 图片直传 COS。
//
// 两步：先找服务端要一个短时效的直传地址，再把文件字节 PUT 上去。
// 小程序端不持有任何密钥——包能被反编译，密钥打进客户端等于把桶公开。
// 图片字节不经服务端中转，省带宽也省一次落盘。
//
// ⚠️ COS 域名要加进小程序后台的 request 合法域名，否则真机上传会被拦。
// 直传地址由服务端按它自己的 COS_BUCKET 现签，所以**两个桶都要加**——
// 开发版打到本地/开发后端时签的是开发桶，正式版打到生产后端时签的是生产桶：
//    https://antonycasa-pro-1327365963.cos.ap-shanghai.myqcloud.com   （生产）
//    https://antony-casa-dev-1327365963.cos.ap-shanghai.myqcloud.com  （开发）

const { send } = require('./http.js')

const MAX_BYTES = 10 * 1024 * 1024
const ALLOWED_EXTS = ['jpg', 'jpeg', 'png', 'webp', 'heic']

function extOf(filePath) {
  const m = /\.([a-zA-Z0-9]+)$/.exec(filePath || '')
  const ext = m ? m[1].toLowerCase() : ''
  // 微信临时文件有时没后缀，默认按 jpg 传
  return ALLOWED_EXTS.includes(ext) ? ext : 'jpg'
}

function requestUploadUrl(scene, ext) {
  return send({ url: '/api/upload-url', method: 'POST', data: { scene, ext } }).then(res => {
    if (res.statusCode === 200 && res.data.ok) return res.data
    throw new Error(res.data.message || '拿不到上传地址')
  })
}

function putBytes(url, buffer) {
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: 'PUT',
      data: buffer,
      // 这个头没参与签名，COS 不校验它，随便给个二进制类型即可
      header: { 'Content-Type': 'application/octet-stream' },
      success: res => {
        if (res.statusCode === 200) return resolve()
        // COS 拒绝时正文里有 <Code>/<Message>，说明是签名过期还是权限不对，
        // 不打出来的话页面上只剩一个光秃秃的状态码
        console.error('[upload] COS 拒绝了这次 PUT', res.statusCode, res.data)
        reject(new Error(`图片上传失败（${res.statusCode}）`))
      },
      fail: err => {
        // 最常见的是 COS 域名没加进小程序后台的 request 合法域名——
        // 微信会在 errMsg 里明说 "url not in domain list"，吞掉它就只剩一句
        // 「网络异常」，根本没法定位。域名要求见本文件顶部的注释
        console.error('[upload] PUT 发不出去', url.split('?')[0], err)
        reject(new Error('网络异常，图片没传上去'))
      }
    })
  })
}

/**
 * 传一张图，成功后返回 COS 对象键。
 * 存进库和往后端提交的都是这个键，不是 URL——桶和地域将来会变，URL 由服务端现签。
 */
async function uploadImage(filePath, scene) {
  const ext = extOf(filePath)

  let buffer
  try {
    buffer = wx.getFileSystemManager().readFileSync(filePath)
  } catch (e) {
    throw new Error('读取图片失败')
  }

  if (buffer.byteLength > MAX_BYTES) {
    throw new Error('单张图片不能超过 10MB')
  }

  const { url, key } = await requestUploadUrl(scene, ext)
  await putBytes(url, buffer)
  return key
}

module.exports = { uploadImage, MAX_BYTES }
