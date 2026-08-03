<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ProductDrawer from './ProductDrawer.vue'

// 左侧完全照 mulook 的做法：**逐级下钻**——点一项，整个列表被它的下一级替换，
// 顶部留一串可点回退的面包屑。这样和原站并排核对时，操作路径是一模一样的。
const all = ref([])
const trail = ref([])          // 已钻进的层级
const selected = ref(null)     // 当前选中的分类
const keyword = ref('')
const colors = ref([])       // 色系字典
const colorId = ref('')      // 选中的色系
const attrs = ref([])        // 当前分类的筛选维度

const products = ref([])
const total = ref(0)
const pageNo = ref(1)
const pageSize = 30
const loading = ref(false)

// 详情走右侧抽屉，不跳页面——和原站一致（原站点卡片也只是往 URL 加 ?skuId=）。
// 选中项放进 query：刷新能还原，也方便拿同一个 skuId 去原站对照。
const route = useRoute()
const router = useRouter()
const openSpu = computed(() => route.query.spu || '')
const openSku = computed(() => route.query.skuId || '')
const openCard = (p) =>
  router.push({ query: { ...route.query, spu: p.spu_id, skuId: p.sku_id } })
const closeDrawer = () => {
  const q = { ...route.query }
  delete q.spu
  delete q.skuId
  router.push({ query: q })
}

// 顺序照抄原站侧栏（sort_order 就是抓取时从上到下的次序）。
// 按商品数重排会让人对照时找不到位置。
const childrenOf = (parentId) =>
  all.value
    .filter((c) => c.parent_id === parentId)
    .sort((a, b) => a.sort_order - b.sort_order)

// 当前该显示哪一层：没钻进去就是一级分类，钻进去了就是最后一层的子级
const currentLevel = computed(() =>
  childrenOf(trail.value.length ? trail.value[trail.value.length - 1].id : null)
)

async function load(reset = false) {
  if (reset) { pageNo.value = 1; products.value = [] }
  loading.value = true
  const q = new URLSearchParams({ pageNo: pageNo.value, pageSize })
  if (selected.value) q.set('categoryId', selected.value.id)
  if (keyword.value.trim()) q.set('keyword', keyword.value.trim())
  if (colorId.value) q.set('colorId', colorId.value)
  const data = await (await fetch(`/api/products?${q}`)).json()
  total.value = data.total
  products.value = reset ? data.list : [...products.value, ...data.list]
  loading.value = false
}

function pickColor(c) {
  colorId.value = colorId.value === c.id ? '' : c.id   // 再点一次取消
  load(true)
}

async function loadAttrs() {
  attrs.value = selected.value
    ? await (await fetch(`/api/filters?categoryId=${selected.value.id}`)).json()
    : []
}

function pick(cat) {
  selected.value = cat
  // 有下级就钻进去，没有（叶子）就只当筛选条件——跟原站行为一致
  if (childrenOf(cat.id).length) trail.value = [...trail.value, cat]
  load(true)
  loadAttrs()
}

function backTo(index) {
  trail.value = trail.value.slice(0, index)
  selected.value = index ? trail.value[index - 1] : null
  load(true)
  loadAttrs()
}

// 只看数字够不够齐，不再画进度条
const pct = (c) => (c.img_total ? Math.round((c.img_local / c.img_total) * 100) : 0)
const barClass = (c) => (c.done ? 'done' : pct(c) > 0 ? 'part' : 'none')

const price = (row) => {
  if (row.min_price == null) return '价格未公开'
  const lo = Number(row.min_price)
  const hi = Number(row.max_price)
  return lo === hi ? `￥${lo}` : `￥${lo} ~ ${hi}`
}

// 本轮已加载的商品里，封面有多少张真在本地——核验下载覆盖率用
const coverStat = computed(() => ({
  local: products.value.filter((p) => p.coverLocal).length,
  withCover: products.value.filter((p) => p.cover_image).length,
}))

onMounted(async () => {
  all.value = await (await fetch('/api/categories')).json()
  colors.value = await (await fetch('/api/colors')).json()
  load(true)
})

watch(pageNo, () => load(false))
</script>

<template>
  <div class="page">
    <aside class="side">
      <el-input v-model="keyword" placeholder="搜索名称 / 型号 / 编号" clearable
                @keyup.enter="load(true)" @clear="load(true)" />

      <!-- 面包屑：点 × 退回该层，跟 mulook 的 chip 一样 -->
      <div v-if="trail.length" class="crumbs">
        <el-tag size="small" effect="plain" style="cursor:pointer" @click="backTo(0)">全部</el-tag>
        <el-tag v-for="(c, i) in trail" :key="c.id" size="small" closable
                @close="backTo(i)">{{ c.name }}</el-tag>
      </div>

      <ul class="cats">
        <li v-for="c in currentLevel" :key="c.id"
            :class="{ on: selected && selected.id === c.id }" @click="pick(c)">
          <div class="row1">
            <span class="nm">
              <b v-if="c.done" class="ok" title="该分类已采集完成">✓</b>{{ c.name }}
            </span>
            <span class="ct">
              {{ c.spu_count }}
              <i v-if="!c.is_leaf" class="arrow">›</i>
            </span>
          </div>
          <!-- 图片下载进度：这个站是用来核验采集的，光有总量看不出采到哪了 -->
          <div class="prog" :class="barClass(c)">
            <template v-if="c.done">已采集完成 · {{ c.img_total }} 张</template>
            <template v-else>图 {{ c.img_local }} / {{ c.img_total }}</template>
          </div>
        </li>
      </ul>

      <!-- 色系。原站侧栏下半部就是这排色卡。 -->
      <div class="fgroup">
        <h4>色系 <span class="dim">{{ colors.length }}</span></h4>
        <div class="swatches">
          <button v-for="c in colors" :key="c.id" class="sw"
                  :class="{ on: colorId === c.id }"
                  :title="`${c.name} · ${c.sku_count} 条`"
                  @click="pickColor(c)">
            <img :src="`/img?u=${encodeURIComponent(c.icon)}&cdn=1`" />
            <span>{{ c.name.replace('系', '') }}</span>
          </button>
        </div>
      </div>

      <!-- 该分类的品类专属筛选维度（厚度/处理面/纹理…），随分类变化 -->
      <div v-if="attrs.length" class="fgroup">
        <h4>该分类的筛选维度 <span class="dim">{{ attrs.length }}</span></h4>
        <div v-for="a in attrs" :key="a.id" class="attr">
          <div class="an">{{ a.name }}</div>
          <div class="av">
            <el-tag v-for="o in a.options" :key="o" size="small" effect="plain">{{ o }}</el-tag>
            <span v-if="!a.options.length" class="dim">（自由填写）</span>
          </div>
        </div>
      </div>
    </aside>

    <main class="main">
      <div class="bar">
        <b>{{ total.toLocaleString() }}</b> 条
        <span class="dim">（与原站同为 SKU 粒度）</span>
        <span v-if="selected" class="dim">· {{ selected.path.join(' › ') }}</span>
        <span class="spacer" />
        <span class="dim">封面本地 {{ coverStat.local }} / {{ coverStat.withCover }}</span>
      </div>

      <div class="grid">
        <!-- 一个 SKU 一张卡，和原站一致。点进去看的是它所属的 SPU 详情。 -->
        <div v-for="p in products" :key="p.sku_id" class="card" @click="openCard(p)">
          <div class="thumb">
            <!-- 只引用本地图。没下到的显示占位，不偷偷回落 CDN，
                 否则「图片正常」的假象会掩盖真实缺口。 -->
            <img v-if="p.coverLocal" :src="`/img?u=${encodeURIComponent(p.cover_image)}`"
                 loading="lazy" />
            <div v-else-if="p.cover_image" class="miss" title="本地还没有这张图">未下载</div>
            <div v-else class="noimg">无图</div>
            <span v-if="p.is_featured" class="badge">精品</span>
          </div>
          <div class="info">
            <div class="nm">{{ p.name }}</div>
            <div class="model">{{ p.model || '—' }}</div>
            <div class="price">{{ price(p) }}</div>
            <div class="meta">
              {{ p.shop_name || '—' }}
              <span class="dim">| {{ p.domestic_or_imported === 'imported' ? '进口' : '国产' }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="more">
        <el-button v-if="products.length < total" :loading="loading" @click="pageNo++">
          加载更多（{{ products.length }} / {{ total.toLocaleString() }}）
        </el-button>
        <span v-else-if="products.length" class="dim">已全部加载</span>
        <span v-else-if="!loading" class="dim">没有匹配的商品</span>
      </div>
    </main>

    <ProductDrawer :spu-id="openSpu" :sku-id="openSku" @close="closeDrawer" />
  </div>
</template>

<style scoped>
.page { display: flex; gap: 16px; padding: 16px 20px; align-items: flex-start; }
.side { width: 250px; flex: none; background: #fff; border-radius: 8px; padding: 12px;
        position: sticky; top: 72px; max-height: calc(100vh - 90px); overflow: auto; }
.crumbs { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.cats { list-style: none; margin: 8px 0 0; padding: 0; }
.cats li { padding: 7px 10px; border-radius: 6px; cursor: pointer; }
.row1 { display: flex; justify-content: space-between; align-items: center; }
/* 只用数字表达进度：够齐了是绿的、缺了是橙的、没开始是灰的 */
.prog { font-size: 11px; margin-top: 1px; }
.prog.done { color: #16a34a; }
.prog.part { color: #d97706; }
.prog.none { color: #c0c4cc; }
.ok { color: #16a34a; margin-right: 4px; font-weight: 700; }
.cats li:hover { background: #f2f3f5; }
.cats li.on { background: #eaf2ff; color: #2f6feb; }
.cats .ct { font-size: 12px; color: #a3a8b0; display: flex; align-items: center; gap: 4px; }
.cats .arrow { font-style: normal; font-size: 15px; }

.fgroup { margin-top: 14px; border-top: 1px solid #f0f1f3; padding-top: 12px; }
.fgroup h4 { margin: 0 0 8px; font-size: 13px; }
.swatches { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.sw { border: 2px solid transparent; border-radius: 6px; background: none;
      padding: 2px; cursor: pointer; }
.sw:hover { border-color: #c9d6ee; }
.sw.on { border-color: #2f6feb; }
.sw img { width: 100%; aspect-ratio: 1; border-radius: 4px; object-fit: cover; display: block; }
.sw span { display: block; font-size: 10px; color: #646a73; margin-top: 2px;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.attr { margin-bottom: 10px; }
.attr .an { font-size: 12px; color: #646a73; margin-bottom: 4px; }
.attr .av { display: flex; flex-wrap: wrap; gap: 4px; }

.main { flex: 1; min-width: 0; }
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: #646a73; }
.spacer { flex: 1; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 14px; }
.card { cursor: pointer; background: #fff; border-radius: 8px; overflow: hidden; display: block;
        transition: box-shadow .15s; }
.card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.1); }
.thumb { position: relative; aspect-ratio: 1; background: #f0f1f3; }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.noimg, .miss { display: grid; place-items: center; height: 100%; font-size: 13px; }
.noimg { color: #c0c4cc; }
.miss { color: #d97706; background: repeating-linear-gradient(45deg,
        #fff7ed, #fff7ed 8px, #ffedd5 8px, #ffedd5 16px); }
.badge { position: absolute; left: 8px; top: 8px; background: #c8a36c; color: #fff;
         font-size: 11px; padding: 1px 6px; border-radius: 3px; }
.info { padding: 10px; }
.info .nm { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.price { color: #e5484d; margin: 2px 0; }
.meta { font-size: 12px; color: #646a73; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dim { color: #a3a8b0; font-size: 12px; }
.more { display: grid; place-items: center; padding: 24px; }
</style>
