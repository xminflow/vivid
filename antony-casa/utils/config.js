// 后端接入方式。请求怎么发出去见 utils/http.js。
//
// 'cloud'：微信云托管。微信内网直连容器，不用备案域名、不用配 request 合法域名，
//          真机和开发者工具都能用，是默认链路。
// 'local'：直连本机 WSL 里跑的服务，只在改后端时用。127.0.0.1 只有开发者工具连得上，
//          真机必然失败，所以切过去之后别忘了改回来。
const API_MODE = 'cloud'

// 云托管环境。env 是云开发环境 ID，service 是云托管服务名，都在云开发控制台看。
// 切生产环境时换 env。
//
// resourceAppid：环境属于**别的 appid** 时才填，也就是走环境共享（资源复用）的情况。
// 填的是「开通该环境的那个小程序/公众号 appid」，不是本小程序的。留空表示环境就在
// 本小程序名下，直连即可。两种形态的调用方式不同，见 utils/http.js 的 ensureCloud。
//
// 前提：环境共享只支持**同主体**的小程序/公众号之间，跨主体做不到；
// 还要先在资源方的云开发控制台「更多 - 环境共享」里授权给本小程序
const CLOUD = {
  env: 'dev-d0gsva5ooa1952304',
  service: 'vivid-server',
  resourceAppid: ''
}

// API_MODE 为 'local' 时才用得上。端口与 server/Dockerfile 的 PORT、README 保持一致。
// 开发者工具需勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」
const API_BASE = 'http://127.0.0.1:3000'

// 静态素材（品牌实拍图）放 COS，不进小程序包：主包上限 2MB，这批图占 1.1MB，
// 而且它们不参与业务逻辑，换图不该走发版。路径稳定可预测，重新上传同名对象即可生效。
// 上传脚本见 server/scripts/upload_static.py。
// 与用户上传图的区别：那些走 uploads/ 前缀、键随机、地址现签；这里走 static/ 前缀、
// 路径固定、直接公开访问。桶权限将来收紧成私有读时，static/ 下的对象要单独设公有读 ACL
const STATIC_BASE = 'https://antony-casa-dev-1327365963.cos.ap-shanghai.myqcloud.com/static'

module.exports = { API_MODE, CLOUD, API_BASE, STATIC_BASE }
