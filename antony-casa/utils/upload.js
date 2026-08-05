// 图片直传 COS。
//
// 两步：先找服务端要一个短时效的直传地址，再把文件字节 PUT 上去。
// 小程序端不持有任何密钥——包能被反编译，密钥打进客户端等于把桶公开。
// 图片字节不经服务端中转，省带宽也省一次落盘。
//
// ⚠️ COS 域名要加进小程序后台的 request 合法域名，否则真机上传会被拦：
//    https://antony-casa-dev-1327365963.cos.ap-shanghai.myqcloud.com

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
        if (res.statusCode === 200) resolve()
        else reject(new Error(`图片上传失败（${res.statusCode}）`))
      },
      fail: () => reject(new Error('网络异常，图片没传上去'))
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
