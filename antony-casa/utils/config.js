// 后端接入方式。请求怎么发出去见 utils/http.js。
//
// 'cloud'：微信云托管。微信内网直连容器，不用备案域名、不用配 request 合法域名，
//          真机和开发者工具都能用，是默认链路。
// 'local'：直连本机 WSL 里跑的服务，只在改后端时用。127.0.0.1 只有开发者工具连得上，
//          真机必然失败，所以切过去之后别忘了改回来。
const API_MODE = 'cloud'

// 云托管环境。env 是小程序已关联的云开发环境 ID，service 是云托管服务名，
// 两者在微信云托管控制台看。切生产环境时换 env
const CLOUD = {
  env: 'dev-d0gsva5ooa1952304',
  service: 'vivid-server'
}

// API_MODE 为 'local' 时才用得上。端口与 server/Dockerfile 的 PORT、README 保持一致。
// 开发者工具需勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」
const API_BASE = 'http://127.0.0.1:3000'

module.exports = { API_MODE, CLOUD, API_BASE }
