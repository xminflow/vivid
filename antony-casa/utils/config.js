// 后端接入方式与环境划分。请求怎么发出去见 utils/http.js。
//
// 'server'：直连自建服务器（https）。Caddy 收 https 反代到容器里的 FastAPI。
//           域名备案已完成（weelume.com 的主体备案覆盖子域名），证书是 Caddy 自动
//           签发续期的 Let's Encrypt，TLS 1.2/1.3 都通——微信对域名的硬要求都满足了。
//           剩下的一步在后台：「开发管理 - 开发设置 - 服务器域名」里把它加进 request
//           合法域名，**生产和开发两个域名都要加**，没加的话真机报「不在以下 request
//           合法域名列表中」。开发者工具可以勾「不校验合法域名」先跑，但真机不认这个勾。
// 'cloud'：微信云托管。微信内网直连容器，不用备案域名、不用配 request 合法域名。
//          自建服务器之前用的链路，留着做退路——自建机器出问题时改这一行就能切回去。
// 'local'：直连本机跑的服务（连的是**开发库**），127.0.0.1 只有开发者工具连得上。
//
// ---------------------------------------------------------------------------
// 环境怎么分
//
// 判据是微信自带的 envVersion（`wx.getAccountInfoSync().miniProgram.envVersion`），
// 不是手工改常量——手改的做法两个方向都出过事：改成 local 调完忘了改回来，
// 发上去全员连不上；忘了改成 local，就是本地对着生产库调、看不到自己刚上架的商品。
//
//   envVersion    什么时候是这个值              连哪
//   ------------  ----------------------------  --------------------------------
//   develop       开发者工具、预览、真机调试     工具里 → 本机 127.0.0.1
//                                               手机上 → DEV_BASE
//   trial         体验版                        DEV_BASE
//   release       正式版                        SERVER_BASE（生产）
//
// 为什么 develop 还要再按「是不是开发者工具」分一次：手机**连不到**你电脑的
// 127.0.0.1，这是物理约束，所以预览和真机调试只能走一个公网可达的开发域名。
// 反过来在工具里连本机是最顺手的——改完后端刷新就能验，不用先部署。
//
// 「预览」和「真机调试」区分不出来：微信只给 envVersion 这一个版本标识，两者同为
// develop、又都在手机上。别拿 wx.getLaunchOptionsSync().scene 去补——那是启动场景，
// 用户从聊天记录或桌面图标二次打开就变了，当环境判据会飘。
// 好在这两种都是开发者自己在用，本来就该指同一个开发环境。

// 开发环境的后端地址。预览、真机调试、体验版打这里。留空表示还没有，
// 此时这三种场景会退回生产并打警告（见 resolveBase）。
//
// 是生产那台服务器上的另一个容器（api-dev），连同一个 postgres 里的另一个库
// antony_casa_dev，加开发桶。编排见 server/deploy/docker-compose.yml，
// 站点见 server/deploy/Caddyfile。
//
// ⚠️ 它和本地开发**不是同一个库**：本地 server/.env 指的是腾讯云那个开发库，
// 这里是服务器容器内网的库，本地连不到（postgres 没有映射宿主端口）。
// 也就是说在电脑上造的数据，扫码预览时手机上看不到。要给预览环境造数据，
// 去 https://dev.admin.antonycasa.weelume.com/ 打开后台——那份产物和生产后台
// 是同一个 dist，在哪个域名下打开就管哪个库。账号是 .env 里的 DEV_ADMIN_SUPER_*。
//
// 为什么必须是备案域名下的 https：手机连不到开发机的 127.0.0.1（物理约束），
// 而微信不收 http、不收 IP、不收未备案域名，内网穿透也绕不过这一条。
// weelume.com 的主体备案覆盖子域名，所以直接挂了个 dev 子域名。
//
// 已经通了：证书签下来了，https 直接可用（2026-08-12 验过 /health 与 /api/*）。
//
// ⚠️ 但真机上还差一步——公众平台「开发管理 - 开发设置 - 服务器域名」要把
//   https://dev.antonycasa.weelume.com
//   https://antony-casa-dev-1327365963.cos.ap-shanghai.myqcloud.com  （开发桶，直传用）
// 加进 request 合法域名。**预览版在真机上强制校验域名**，开发者工具里那个
// 「不校验合法域名」的勾在手机上不作数，没加就是 url not in domain list。
//
// 填了值就没有退回生产的兜底了（resolveBase 只在这一项为空时才回落），
// 所以将来要停用开发环境，把这里改回空串，别让它指着一个打不通的域名。
const DEV_BASE = 'https://dev.antonycasa.weelume.com'

// 生产。必须是 https——小程序不允许 http 请求。
// 证书由服务器上的 Caddy 自动申请和续期，部署说明见 server/deploy/README.md
const SERVER_BASE = 'https://antonycasa.weelume.com'

// 本机服务，只有开发者工具连得上。端口与 server/Dockerfile 的 PORT、README 一致。
//
// 两个前提，缺一个就是「工具里一片空白但不报错」：
//   1. 本机服务要起着 —— WSL 里 `uv run uvicorn app.main:app`，
//      原生 Windows 用 `uv run python scripts/dev_server.py`（见 server/README.md）
//   2. 开发者工具要勾「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」，
//      否则 http:// 的请求根本发不出去
const LOCAL_BASE = 'http://127.0.0.1:3000'

// 想强制某个模式时填这里（'server' | 'cloud' | 'local'），留空表示自动判断。
// 排查「是不是环境选错了」时最有用。提交前记得清空。
//
// ⚠️ 现在填了 'server'，是为了在开发者工具里验**开发环境**的完整支付链路。
// 不填的话 detectApiMode() 会判成 'local' 打到 127.0.0.1:3000，而本机那个
// 端口现在是别的项目；就算腾出来，本地也没配 WXPAY_*，下单拿不到支付参数，
// 支付→回调→发货→通知这条链在本地根本走不完。
// **测完提交前清空这一行。**
const API_MODE_OVERRIDE = 'server'

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

// 'server' 模式下具体打哪个域名。'local' 和 'cloud' 不走这里
function resolveBase() {
  if (ENV_VERSION === 'release') return SERVER_BASE

  if (ENV_VERSION === 'develop' || ENV_VERSION === 'trial') {
    if (DEV_BASE) return DEV_BASE
    // 显式降级，不是静默兜底：开发环境还没立起来时，宁可让开发版打到生产
    // （数据看得懂、链路是通的），也不能打到一个不存在的域名——那只会得到
    // 一句「网络异常」，谁都看不出是配置没填
    console.warn('[config] DEV_BASE 未配置，开发版/体验版将连接生产环境')
    return SERVER_BASE
  }

  // envVersion 读不到（基础库过老，或被工具的某些模式吃掉）。按生产处理并留下日志：
  // 宁可开发时对着生产调（看得出来），也不能让正式版用户打到开发环境（看不出来）
  console.error('[config] 读不到 envVersion，按生产环境处理')
  return SERVER_BASE
}

// 最终的请求前缀。http.js 直接用它，不再自己判断模式
const HTTP_BASE = API_MODE === 'local' ? LOCAL_BASE : resolveBase()

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

// 静态素材（品牌实拍图）放 COS，不进小程序包：主包上限 2MB，这批图占 1.1MB，
// 而且它们不参与业务逻辑，换图不该走发版。路径稳定可预测，重新上传同名对象即可生效。
// 上传脚本见 server/scripts/upload_static.py。
// 与用户上传图的区别：那些走 uploads/ 前缀、键随机、地址现签；这里走 static/ 前缀、
// 路径固定、直接公开访问。桶权限将来收紧成私有读时，static/ 下的对象要单独设公有读 ACL
//
// ⚠️ 这里写死的是**生产桶**，开发版和体验版也读它。品牌实拍图是仓库里的同一批文件，
// 不随环境变化，没必要按环境分叉；而小程序包只有一份，分叉了反而要在客户端判环境。
// 与之相对，运行时产生的图（用户上传、后台配的首页图与商品图）跟着各自环境的
// COS_BUCKET 走，地址由服务端出，不经过这个常量。
// 换图后要跑 server/scripts/upload_static.py 推到同一个桶，那个脚本默认就指着它
const STATIC_BASE = 'https://antonycasa-pro-1327365963.cos.ap-shanghai.myqcloud.com/static'

module.exports = { API_MODE, ENV_VERSION, HTTP_BASE, CLOUD, STATIC_BASE }
