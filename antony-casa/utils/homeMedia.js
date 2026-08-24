// 首页配图：后台配什么就显示什么。
//
// 三层来源，优先级从高到低：
//   1. 接口 GET /api/home —— 后台「首页图片」页配的，是唯一的事实来源
//   2. 本机缓存 —— 上一次成功拿到的那份。首屏直接用它渲染，不用等网络回来；
//                  断网或后端挂了时也是它顶着，首页不会开天窗
//   3. mock/home.js 里的默认值 —— 从没成功拉到过配置时用（新用户第一次打开就断网、
//                  或者后台根本还没配过图）。这些是固定地址的图，不占小程序包体积
//
// 「某个位置没配 = 用默认值」是和服务端约定好的（见 server/app/home.py）：
// 后台清空某一组，接口返回空数组，这里就退回默认值——任何时候首页都有图可显示。
//
// 代价说明：缓存兜底意味着断网时用户可能看到的是上一次的旧图。这是刻意的取舍，
// 换的是首页永远不空白；每次拉取失败都会打 error 日志，不会悄悄咽掉。

// 只接管三组图，文案仍在 mock/home.js。转发卡片配图不单列一项，直接复用 hero 首图：
// 各页共用同一张品牌形象这点不变，只是这张图现在也跟着后台配置走（见下面的 shareImage）
const { send } = require('./http.js')
const { heroSlides, showroom, activity } = require('../mock/home.js')

// 带版本号：将来出参结构改了，旧缓存不会被当成新结构读进来
const CACHE_KEY = 'homeMedia.v1'

// mock/home.js 里那份默认值，拆成和接口一致的形状，下面只走一套合并逻辑
const DEFAULTS = {
  hero: heroSlides.map(slide => slide.image),
  showroom: showroom.images,
  activity: activity.image
}

function readCache() {
  try {
    const cached = wx.getStorageSync(CACHE_KEY)
    return cached && typeof cached === 'object' ? cached : null
  } catch (e) {
    // 存储读不出来（极少见，比如存储被清理到一半）不该让首页崩掉，当没缓存处理
    console.error('[homeMedia] 读缓存失败', e)
    return null
  }
}

function writeCache(media) {
  try {
    wx.setStorageSync(CACHE_KEY, media)
  } catch (e) {
    // 写不进去只影响下次冷启动的首屏速度，这次展示不受影响
    console.error('[homeMedia] 写缓存失败', e)
  }
}

function pickList(list, fallback) {
  return Array.isArray(list) && list.length ? list : fallback
}

/**
 * 把一份配置合成 setData 能直接吃的形状。空的位置退回默认值。
 * 返回的键对应 pages/index/index.js 的 data 结构，改那边的字段名要一起改。
 */
function toPageData(media) {
  const config = media || {}

  return {
    heroSlides: pickList(config.hero, DEFAULTS.hero).map(image => ({ image })),
    'showroom.images': pickList(config.showroom, DEFAULTS.showroom),
    'activity.image': config.activity || DEFAULTS.activity
  }
}

/** 立刻能拿到的那份：有缓存用缓存，没有就用包内默认值。不发请求。 */
function local() {
  return toPageData(readCache())
}

/**
 * 去服务端拉最新配置，成功后写缓存并返回可 setData 的数据。
 * 失败时抛出，由调用方决定怎么提示——这里不吞异常，也不返回半份数据。
 */
function refresh() {
  return send({ url: '/api/home' }).then(res => {
    if (res.statusCode !== 200 || !res.data || !res.data.ok) {
      throw new Error((res.data && res.data.message) || '首页配置读取失败')
    }

    // 只留自己认识的字段：接口将来加字段不会顺带把缓存撑大
    const media = {
      hero: res.data.hero || [],
      showroom: res.data.showroom || [],
      activity: res.data.activity || null
    }
    console.debug(
      '[homeMedia] 拉到配置 hero=%d showroom=%d activity=%s updatedAt=%s',
      media.hero.length,
      media.showroom.length,
      media.activity ? '有' : '无',
      res.data.updatedAt
    )

    writeCache(media)
    return toPageData(media)
  })
}

/**
 * 转发卡片的配图：首页画廊的第一张，各页共用。
 *
 * 不指定 imageUrl 的话微信拿当前页面截图顶上，截到的可能是轮播随机某一张、也可能
 * 赶上图还没加载完的空白，卡片长什么样完全不可控，所以每个页面都要显式给一张。
 *
 * 只读缓存、不发请求：onShareAppMessage 必须同步返回，等不了网络。没缓存时
 * （用户冷启动直接进了非首页，还没人拉过 /api/home）用包内默认首图，就是后台
 * 配图之前的那张，卡片不会开天窗。
 *
 * ⚠️ 微信按 5:4 居中裁剪、最短边不小于 300px。后台换首图会连带换掉所有页面的
 * 分享卡片，上传时要考虑裁成 5:4 之后画面还成不成立。
 */
function shareImage() {
  const cached = readCache()
  return pickList(cached && cached.hero, DEFAULTS.hero)[0]
}

module.exports = { local, refresh, shareImage }
