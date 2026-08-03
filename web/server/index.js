// 安选材料库的只读 API。
//
// 只为一件事服务：把采集进 anxuan 库的数据原样摆出来，方便和 mulook 原站逐屏对照。
// 因此这里不做任何"业务加工"——不补默认值、不改字段名、价格为 NULL 就显示未公开。
// 一旦加工，对照就失去意义了。

import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import pg from 'pg'

const HERE = path.dirname(fileURLToPath(import.meta.url))
// 图片下载在 scraper 那边，按 <一级分类>/<spuId>/<序号>_<OSS哈希> 存
const IMAGE_ROOT = path.resolve(HERE, '../../scraper/output/mulook/images')
const PORT = Number(process.env.PORT || 5183)

const pool = new pg.Pool({
  connectionString:
    process.env.ANXUAN_DSN ||
    'postgresql://anxuan:s966nAULadboaQdySxlYNDpC@127.0.0.1:5432/anxuan',
  max: 8,
})

// 本地图片索引：OSS 文件名 → 磁盘路径。
// 图片只下了一小部分，没下到的要能回落到原始 CDN URL，
// 否则页面上全是破图，没法对照。
let localImages = new Map()
function indexImages(dir = IMAGE_ROOT) {
  const found = new Map()
  const walk = (d) => {
    let entries
    try {
      entries = fs.readdirSync(d, { withFileTypes: true })
    } catch {
      return
    }
    for (const e of entries) {
      const full = path.join(d, e.name)
      if (e.isDirectory()) walk(full)
      else {
        // 键统一小写：URL 后缀可能是 .JPG，落盘时转成了 .jpg，
        // 不归一化就会把已下载的图判成缺失。
        const i = e.name.indexOf('_')
        found.set((i > 0 ? e.name.slice(i + 1) : e.name).toLowerCase(), full)
      }
    }
  }
  walk(dir)
  return found
}

const CONTENT_TYPE = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
  gif: 'image/gif', webp: 'image/webp', avif: 'image/avif',
}

const json = (res, body, status = 200) => {
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'access-control-allow-origin': '*',
  })
  res.end(JSON.stringify(body))
}

const routes = {
  // 分类树。一次全给——402 条而已，前端自己组装层级比来回请求省事。
  async '/api/categories'() {
    // 每个分类的图片下载进度。把本地文件名推给 PG 去比对（几千个名字），
    // 比把 32 万行 image 拉回 Node 里数便宜得多。
    const names = [...localImages.keys()]
    const { rows: prog } = await pool.query(
      `select s.category_id as cid,
              count(*)::int as img_total,
              count(*) filter (where lower(i.oss_name) = any($1))::int as img_local
         from image i join spu s on s.id = i.spu_id
        group by s.category_id`,
      [names]
    )
    const byLeaf = new Map(prog.map((r) => [r.cid, r]))

    const { rows } = await pool.query(
      // 两点必须和原站对齐：
      // 1) 顺序用 sort_order（抓取时侧栏从上到下的原始次序），不能按数量重排；
      // 2) 计数要**含整棵子树**——商品都挂在叶子上，一级分类自己没有直接商品，
      //    按 category_id 直接数会全是 0。
      `select c.id, c.parent_id, c.name, c.level, c.path, c.is_leaf, c.sort_order,
              (select count(*) from spu
                 join category d on d.id = spu.category_id
                where d.path[1:array_length(c.path,1)] = c.path)::int as spu_count
         from category c order by c.sort_order`
    )

    // 商品挂在叶子上，所以非叶子的进度要把整棵子树的图片累加起来。
    // 用 path 前缀判断隶属关系，跟 spu_count 一个套路。
    const isUnder = (leafPath, myPath) =>
      myPath.every((seg, i) => leafPath[i] === seg)
    const pathOf = new Map(rows.map((r) => [r.id, r.path]))
    return rows.map((r) => {
      let total = 0
      let local = 0
      for (const [cid, p] of byLeaf) {
        const lp = pathOf.get(cid)
        if (lp && isUnder(lp, r.path)) {
          total += p.img_total
          local += p.img_local
        }
      }
      // 「完成」= 这个分类下有商品、且图片一张不缺。
      // 只看图片就够了：图片记录本身来自详情，详情没抓过的分类 img_total 就是 0。
      const done = total > 0 && local >= total
      return { ...r, img_total: total, img_local: local, done }
    })
  },

  // 商品列表。**按 SKU 粒度**——原站列表就是一个规格一条，
  // 同一款镜子的 93 个规格在它那儿就是 93 条。核验时两边数字必须对得上，
  // 按 SPU 归并会显示 59 款，看着就像漏采了。
  async '/api/products'(q) {
    const limit = Math.min(Number(q.get('pageSize') || 30), 100)
    const offset = (Math.max(Number(q.get('pageNo') || 1), 1) - 1) * limit
    const where = []
    const args = []

    if (q.get('categoryId')) {
      // 选中的若是非叶子分类，要把它整棵子树的商品都算上，
      // 这正是 path 数组存在的意义：一次匹配，不必递归。
      args.push(q.get('categoryId'))
      where.push(`spu.category_id in (
        select c2.id from category c1 join category c2
          on c2.path[1:array_length(c1.path,1)] = c1.path
        where c1.id = $${args.length})`)
    }
    if (q.get('keyword')) {
      args.push(`%${q.get('keyword')}%`)
      where.push(`(spu.name ilike $${args.length} or spu.platform_no ilike $${args.length}
                   or sku.model ilike $${args.length})`)
    }
    if (q.get('shopId')) {
      args.push(q.get('shopId'))
      where.push(`spu.shop_id = $${args.length}`)
    }
    if (q.get('colorId')) {
      args.push(q.get('colorId'))
      where.push(`sku.color = $${args.length}`)
    }
    const filter = where.length ? `where ${where.join(' and ')}` : ''

    const { rows: countRows } = await pool.query(
      `select count(*)::int as total from sku join spu on spu.id = sku.spu_id ${filter}`,
      args
    )
    const { rows } = await pool.query(
      `select sku.id as sku_id, sku.model, sku.min_price, sku.max_price,
              sku.cover_image, sku.is_sample,
              color.name as color_name, color.icon as color_icon,
              spu.id as spu_id, spu.name, spu.category_id, spu.shop_id, spu.is_featured,
              spu.domestic_or_imported, spu.measurement_unit,
              shop.name as shop_name, area.name as origin_name,
              (select count(*)::int from sku s2 where s2.spu_id = spu.id) as sku_count
         from sku join spu on spu.id = sku.spu_id
                  left join shop on shop.id = spu.shop_id
                  left join color on color.id = sku.color
                  left join area on area.id = spu.origin
         ${filter} order by spu.is_featured desc, spu.id, sku.id
         limit ${limit} offset ${offset}`,
      args
    )
    // 顺带告诉前端封面在不在本地，列表页就能直观看出下载覆盖率
    const list = rows.map((r) => ({
      ...r,
      coverLocal: r.cover_image
        ? localImages.has(r.cover_image.split('?')[0].split('/').pop().toLowerCase())
        : false,
    }))
    return { total: countRows[0].total, list }
  },

  // 商品详情：SPU + 全部 SKU + 图片 + 品类参数
  async '/api/product'(q) {
    const id = q.get('id')
    if (!id) return { error: 'missing id' }
    const [spu, skus, imageRows, spuAttrs, skuAttrs] = await Promise.all([
      pool.query(
        `select spu.*, shop.name as shop_name, shop.logo as shop_logo,
                shop.company_name, shop.contact_name, category.path as category_path,
                brand.name as brand_name, area.name as origin_name
           from spu left join shop on shop.id = spu.shop_id
                    left join category on category.id = spu.category_id
                    left join brand on brand.id = spu.brand_id
                    left join area on area.id = spu.origin
          where spu.id = $1`,
        [id]
      ),
      // 色系是雪花号，必须关联字典才有名字——不然页面上只能显示一串 ID
      pool.query(
        `select sku.*, color.name as color_name, color.icon as color_icon
           from sku left join color on color.id = sku.color
          where sku.spu_id = $1 order by sku.id`,
        [id]
      ),
      pool.query("select * from image where spu_id = $1 order by kind, ordinal", [id]),
      pool.query('select * from spu_attr where spu_id = $1', [id]),
      pool.query(
        'select a.* from sku_attr a join sku on sku.id = a.sku_id where sku.spu_id = $1',
        [id]
      ),
    ])
    if (!spu.rows[0]) return { error: 'not found' }
    // 直接告诉前端每张图有没有本地文件，别让它靠 404 去猜
    const images = imageRows.rows.map((i) => ({
      ...i,
      local: localImages.has((i.oss_name || '').toLowerCase()),
    }))
    return {
      ...spu.rows[0],
      skus: skus.rows,
      images,
      imageStats: { total: images.length, local: images.filter((i) => i.local).length },
      attrs: spuAttrs.rows,
      skuAttrs: skuAttrs.rows,
    }
  },

  // 色系字典。侧栏的色卡筛选用它。
  async '/api/colors'() {
    const { rows } = await pool.query(
      `select color.id, color.name, color.icon,
              (select count(*)::int from sku where sku.color = color.id) as sku_count
         from color order by color.id`
    )
    return rows
  },

  // 某个分类下有哪些筛选维度。组的构成随分类变化，所以要带 categoryId。
  async '/api/filters'(q) {
    const id = q.get('categoryId')
    if (!id) return []
    const { rows } = await pool.query(
      `select a.id, a.name, a.en_name, a.type,
              coalesce(array_agg(o.value order by o.sort_order)
                       filter (where o.value is not null), '{}') as options
         from category_attr a
         left join category_attr_option o
                on o.category_id = a.category_id and o.attr_id = a.id
        where a.category_id = $1
        group by a.id, a.name, a.en_name, a.type, a.sort_order
        order by a.sort_order`,
      [id]
    )
    return rows
  },

  async '/api/shops'() {
    const { rows } = await pool.query(
      `select shop.id, shop.name, shop.company_name,
              count(spu.id)::int as spu_count
         from shop left join spu on spu.shop_id = shop.id
        group by shop.id order by spu_count desc`
    )
    return rows
  },

  // 采集进度：本地图片覆盖了多少，页面上直接能看出哪些是本地图、哪些还在走 CDN
  async '/api/stats'() {
    const { rows } = await pool.query(
      `select (select count(*)::int from category) as categories,
              (select count(*)::int from spu) as spus,
              (select count(*)::int from sku) as skus,
              (select count(*)::int from image) as images,
              (select count(*)::int from shop) as shops`
    )
    return { ...rows[0], localImages: localImages.size }
  },
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')

  // 图片。**默认只发本地文件**：这个站是用来核验采集结果的，
  // 悄悄回落到 CDN 会让「图片下载正常」的假象掩盖真实缺口。
  // 缺的一律返回 404，前端画成占位块；确实想看原图时加 ?cdn=1 才回落。
  if (url.pathname === '/img') {
    const src = url.searchParams.get('u') || ''
    const name = decodeURIComponent(src.split('?')[0].split('/').pop() || '').toLowerCase()
    const hit = localImages.get(name)
    if (hit && fs.existsSync(hit)) {
      res.writeHead(200, {
        'content-type': CONTENT_TYPE[path.extname(hit).slice(1).toLowerCase()] || 'image/jpeg',
        'cache-control': 'public, max-age=86400',
        'x-source': 'local',
      })
      fs.createReadStream(hit).pipe(res)
      return
    }
    if (url.searchParams.get('cdn') === '1') {
      // 必须转义：色卡图的文件名带中文（彩色@2x.png），
      // 原样塞进 Location 头会抛 ERR_INVALID_CHAR 把整个进程带崩。
      res.writeHead(302, { location: encodeURI(src), 'x-source': 'cdn' })
      res.end()
      return
    }
    res.writeHead(404, { 'x-source': 'missing', 'access-control-allow-origin': '*' })
    res.end()
    return
  }

  const handler = routes[url.pathname]
  if (!handler) return json(res, { error: 'not found' }, 404)
  try {
    json(res, await handler(url.searchParams))
  } catch (err) {
    console.error(url.pathname, err)
    json(res, { error: String(err) }, 500)
  }
})

// 兜底：一个坏请求不该把整个服务拖垮。之前色卡图 URL 带中文塞进 Location 头，
// 抛 ERR_INVALID_CHAR 直接让进程退出，前端所有接口一起挂掉。
process.on('uncaughtException', (err) => console.error('未捕获异常：', err.message))
process.on('unhandledRejection', (err) => console.error('未处理的 rejection：', err))

localImages = indexImages()

// 采集是一直在跑的，索引只在启动时扫一次的话，新下的图永远显示成「未下载」，
// 这个站就失去了核验的意义。定时重扫一遍，几万个文件也就几百毫秒。
setInterval(() => {
  const t0 = Date.now()
  const next = indexImages()
  if (next.size !== localImages.size) {
    console.log(`本地图片索引 ${localImages.size} → ${next.size}（扫描 ${Date.now() - t0}ms）`)
    localImages = next
  }
}, 60_000).unref?.()

server.listen(PORT, () => {
  console.log(`API  http://localhost:${PORT}`)
  console.log(`本地图片索引 ${localImages.size} 张（其余回落 CDN）`)
})
