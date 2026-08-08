/**
 * 图片直传 COS。
 *
 * 两步：先找服务端要一个短时效的直传地址，再把文件字节 PUT 上去。
 * 浏览器端不持有任何密钥——前端产物是公开的，密钥打进去等于把桶公开。
 * 图片字节不经服务端中转，省带宽也省一次落盘。
 *
 * ⚠️ 与小程序的区别：浏览器受同源策略约束，这个 PUT 是跨域请求，而且带了
 *    Content-Type 头属于「非简单请求」，会先发一次 OPTIONS 预检。
 *    所以 COS 桶必须配 CORS 规则放行本站来源 + PUT + OPTIONS，
 *    否则上传会在预检阶段就被浏览器拦掉（小程序不走 CORS，配置漏了也发现不了）。
 *    配置方法见 antony-web/README.md。
 */
import { requestUploadUrl } from './api'

/** 与服务端 app/cos.py 的 ALLOWED_EXTS 保持一致，多一个少一个都会被拒 */
const ALLOWED_EXTS = ['jpg', 'jpeg', 'png', 'webp', 'heic'] as const

/** 服务端 cos.MAX_UPLOAD_BYTES 也会校验一次，这里只是为了选完文件立刻给反馈，
    不用等传完一个几十兆的文件才被拒 */
export const MAX_BYTES = 10 * 1024 * 1024

export const ACCEPT_ATTR = 'image/jpeg,image/png,image/webp,image/heic'

function extOf(file: File): string {
  const m = /\.([a-zA-Z0-9]+)$/.exec(file.name)
  const ext = m ? m[1].toLowerCase() : ''
  if ((ALLOWED_EXTS as readonly string[]).includes(ext)) return ext
  // 有些相机和 iOS 分享出来的文件没有后缀，退回按 MIME 判断，再不行按 jpg 传
  const fromMime = file.type.split('/')[1]?.toLowerCase() ?? ''
  return (ALLOWED_EXTS as readonly string[]).includes(fromMime) ? fromMime : 'jpg'
}

/**
 * 传一张图，成功后返回 COS 对象键。
 * 存进库和往后端提交的都是这个键，不是 URL——桶和地域将来会变，URL 由服务端现签。
 */
export async function uploadImage(file: File, scene: string): Promise<string> {
  if (file.size > MAX_BYTES) {
    throw new Error('单张图片不能超过 10MB')
  }

  const { url, key } = await requestUploadUrl(scene, extOf(file))

  let res: Response
  try {
    res = await fetch(url, {
      method: 'PUT',
      // 这个头没参与签名，COS 不校验它的值，但它会让请求变成非简单请求触发预检
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    })
  } catch (cause) {
    // 最常见的原因是桶没配 CORS：浏览器在预检阶段就拒了，fetch 直接抛，
    // 拿不到任何状态码。控制台里能看到具体的 CORS 报错
    console.error('[upload] PUT 失败，检查 COS 桶的 CORS 配置', cause)
    throw new Error('图片没传上去，请稍后重试')
  }

  if (!res.ok) {
    throw new Error(`图片上传失败（${res.status}）`)
  }
  return key
}
