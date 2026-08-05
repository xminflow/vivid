// 接口地址。开发阶段指向 WSL 里跑的本地服务，
// 开发者工具需勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」。
// 上线前换成备案好的 https 域名，并在小程序后台配置 request 合法域名。
const API_BASE = 'http://127.0.0.1:3000'

module.exports = { API_BASE }
