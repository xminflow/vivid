// 后端接入方式与环境划分。请求怎么发出去见 utils/http.js。
//
// 只有两种模式，没有 antony-casa 那个 'cloud'（微信云托管）：那是安东尼之家早期
// 的迁移遗留，安家立业从没用过。抄过来就是一段永远不会执行、却要求每个读代码的人
// 先理解一遍的分支。
//
// 'server'：直连自建服务器（https）。Caddy 收 https 反代到容器里的 FastAPI。
// 'local'：直连本机跑的服务（连的是开发库 anjia_dev），只有开发者工具连得上。
//
// ---------------------------------------------------------------------------
// 环境怎么分
//
// 判据是微信自带的 envVersion（`wx.getAccountInfoSync().miniProgram.envVersion`），
// 不是手工改常量——手改的做法两个方向都出过事：改成 local 调完忘了改回来，
// 发上去全员连不上；忘了改成 local，就是本地对着线上库调。
//
//   envVersion    什么时候是这个值              连哪
//   ------------  ----------------------------  --------------------------------
//   develop       开发者工具、预览、真机调试     工具里 → 本机 127.0.0.1
//                                               手机上 → DEV_BASE
//   trial         体验版                        DEV_BASE
//   release       正式版                        SERVER_BASE
//
// 手机连不到你电脑的 127.0.0.1，这是物理约束，所以预览和真机调试只能走一个
// 公网可达的开发域名。

// 开发环境的后端地址。预览、真机调试、体验版打这里。
//
// 域名已经定了是 dev.anjia.weelume.com —— **不复用** 安东尼之家的
// dev.antonycasa.weelume.com，虽然两者反代到的是同一个容器（后端是一个进程装
// 整个矩阵，靠 /api/anjia/ 前缀分流，见 docs/adr/0001）。分开是因为域名一旦
// 发出去就绑在小程序的审核发版上，而进程拓扑随时能改：这样将来拆容器只要改
// Caddy 的 upstream，小程序不用发版。
//
// 但这里**先留空**，解析和证书还没做。填一个打不通的域名比留空更糟——留空只是
// 走不了预览（http.js 会给一句说得清的话），填错了得到的是「网络异常」，
// 没人看得出是配置问题。
//
// 要开预览时三件事，十分钟：DNS 一条记录、Caddy 加一段站点、在**安家立业自己的**
// appid（wx89c6660cf345877f）后台把它加进 request 合法域名。
// ⚠️ 最后一步用安东尼之家的后台加不算数：两个 appid 两套独立的合法域名列表，
// 加错的表现是真机 url not in domain list，而开发者工具里勾了「不校验合法域名」
// 一切正常——工具和真机结论相反，这种错最费时间。
const DEV_BASE = ''

// 生产。安家立业尚未发布，域名定为 anjia.weelume.com。必须是 https。
const SERVER_BASE = ''

// 本机服务，只有开发者工具连得上。
// 与安东尼之家是**同一个进程、同一个端口**，路径前缀 /api/anjia/ 是分界。
//
// 两个前提，缺一个就是「工具里一片空白但不报错」：
//   1. 本机服务要起着 —— WSL 里 `uv run uvicorn app.main:app`，
//      原生 Windows 用 `uv run python scripts/dev_server.py`（见 server/README.md）
//   2. 开发者工具要勾「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」，
//      否则 http:// 的请求根本发不出去
const LOCAL_BASE = 'http://127.0.0.1:3000'

// 想强制某个模式时填这里（'server' | 'local'），留空表示自动判断。
// 排查「是不是环境选错了」时最有用。提交前记得清空。
const API_MODE_OVERRIDE = ''

function readEnvVersion() {
  // 拿不到就返回空串，由调用方决定怎么办——这里不替它猜
  try {
    const account = wx.getAccountInfoSync && wx.getAccountInfoSync()
    return (account && account.miniProgram && account.miniProgram.envVersion) || ''
  } catch (err) {
    return ''
  }
}

function isDevtools() {
  // getDeviceInfo 是新接口，getSystemInfoSync 在旧基础库上顶一下
  let platform = ''
  try {
    platform = (wx.getDeviceInfo && wx.getDeviceInfo().platform) || ''
  } catch (err) {
    platform = ''
  }
  if (!platform) {
    try {
      platform = (wx.getSystemInfoSync && wx.getSystemInfoSync().platform) || ''
    } catch (err) {
      platform = ''
    }
  }
  return platform === 'devtools'
}

const ENV_VERSION = readEnvVersion()

function detectApiMode() {
  if (API_MODE_OVERRIDE) return API_MODE_OVERRIDE
  // 只有开发者工具能连 127.0.0.1，且只在开发版里这么干——
  // 体验版哪怕在工具里打开，也该走开发域名，那才是它要验的链路
  if (ENV_VERSION === 'develop' && isDevtools()) return 'local'
  return 'server'
}

const API_MODE = detectApiMode()

// 'server' 模式下具体打哪个域名。可能返回空串——安家立业这两个地址都还没有。
//
// 这里**不抛异常**：HTTP_BASE 是模块加载期求值的，而 http.js 在顶部 require 本模块、
// 页面又 require http.js。在这里抛，抛的时机是小程序启动加载第一个页面模块的时候，
// 结果不是「弹一句明确的错误」，而是整个小程序白屏，真正的原因埋在模块加载栈里。
// 判空交给 http.js，在**发请求的那一刻**报，页面照常渲染。
function resolveBase() {
  if (ENV_VERSION === 'release') return SERVER_BASE
  if (ENV_VERSION === 'develop' || ENV_VERSION === 'trial') return DEV_BASE

  // envVersion 读不到（基础库过老，或被工具的某些模式吃掉）。按生产处理并留下日志：
  // 宁可开发时对着生产调（看得出来），也不能让正式版用户打到开发环境（看不出来）
  console.error('[config] 读不到 envVersion，按生产环境处理')
  return SERVER_BASE
}

// 最终的请求前缀。http.js 直接用它，不再自己判断模式
const HTTP_BASE = API_MODE === 'local' ? LOCAL_BASE : resolveBase()

module.exports = { API_MODE, ENV_VERSION, HTTP_BASE }
