/**
 * 接入配置。
 *
 * 官网和接口同域（都在 antonycasa.weelume.com 上，由 Caddy 分流），所以请求一律用
 * 相对路径，不拼域名——写死域名的话本地开发、换域名、加 CDN 都要跟着改。
 * 开发环境由 vite 的 server.proxy 把 /api 转给后端，见 vite.config.ts。
 */
export const API_BASE = ''

/**
 * 品牌实拍图放在 COS 上，不进构建产物：这批图有十几兆，换图也不该走一次发版。
 * 与小程序 utils/config.js 的 STATIC_BASE 是同一个前缀、同一批文件，
 * 换图只要跑 server/scripts/upload_static.py 覆盖上去，两端同时生效。
 */
export const STATIC_BASE =
  'https://antony-casa-dev-1327365963.cos.ap-shanghai.myqcloud.com/static'
