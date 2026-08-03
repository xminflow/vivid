<script setup>
import { ref, computed, watch } from 'vue'

// 详情以右侧抽屉打开，和原站一致——点商品卡片不跳页面，只是往 URL 加 ?skuId=。
const props = defineProps({
  spuId: { type: String, default: '' },
  skuId: { type: String, default: '' },   // 从哪张卡片点进来的，用来预选规格
})
const emit = defineEmits(['close'])

const KIND_LABEL = { cover: '封面', case: '案例图', dim: '尺寸图', struct: '结构图' }

const data = ref(null)
const activeSku = ref(null)
const loading = ref(false)

watch(
  () => props.spuId,
  async (id) => {
    if (!id) { data.value = null; return }
    loading.value = true
    data.value = null
    const d = await (await fetch(`/api/product?id=${id}`)).json()
    data.value = d
    // 点哪张卡就选中哪个规格；直接开链接时退回第一个
    activeSku.value = d.skus?.find((s) => s.id === props.skuId) || d.skus?.[0] || null
    loading.value = false
  },
  { immediate: true }
)

// 封面和尺寸图跟着规格走：一款有 35 个规格就有 35 张封面，
// 平铺会让人以为"一个商品配了 35 张封面"。案例图、结构图才是整款共用的。
const groups = computed(() => {
  if (!data.value) return []
  const mine = (img) =>
    img.sku_id == null || (activeSku.value && img.sku_id === activeSku.value.id)
  const by = {}
  for (const img of data.value.images) {
    if ((img.kind === 'cover' || img.kind === 'dim') && !mine(img)) continue
    ;(by[img.kind] ||= []).push(img)
  }
  return ['cover', 'case', 'struct', 'dim']
    .filter((k) => by[k])
    .map((k) => ({ kind: k, label: KIND_LABEL[k], items: by[k] }))
})

const allStats = computed(() => {
  if (!data.value) return {}
  const by = {}
  for (const img of data.value.images) {
    const s = (by[img.kind] ||= { total: 0, local: 0 })
    s.total++
    if (img.local) s.local++
  }
  return by
})

const skuAttrs = computed(() =>
  activeSku.value ? data.value.skuAttrs.filter((a) => a.sku_id === activeSku.value.id) : []
)

// 规格切换器上的小图：优先用该 SKU 自己的封面
const skuThumb = (s) => (s.cover_image ? `/img?u=${encodeURIComponent(s.cover_image)}` : null)

const price = (s) =>
  !s || s.min_price == null
    ? '价格未公开'
    : Number(s.min_price) === Number(s.max_price)
      ? `￥${Number(s.min_price)}`
      : `￥${Number(s.min_price)} ~ ${Number(s.max_price)}`
</script>

<template>
  <!-- 必须留 header：去掉它连自带的关闭按钮也没了，抽屉就关不掉。
       同时允许点遮罩和按 ESC 关闭。 -->
  <el-drawer :model-value="!!spuId" direction="rtl" size="62%"
             :close-on-click-modal="true" :close-on-press-escape="true"
             @update:model-value="(v) => !v && emit('close')"
             @close="emit('close')">
    <template #header>
      <span class="dtitle">{{ data?.name || '商品详情' }}</span>
    </template>

    <div v-if="loading" class="pad dim">加载中…</div>

    <div v-else-if="data && !data.error" class="body">
      <div class="head">
        <div class="crumb dim">{{ (data.category_path || []).join(' › ') }}</div>
        <h2>
          {{ data.name }}
          <el-tag v-if="data.imageStats" size="small"
                  :type="data.imageStats.local === data.imageStats.total ? 'success' : 'warning'">
            图片 {{ data.imageStats.local }} / {{ data.imageStats.total }}
          </el-tag>
        </h2>
        <div class="sub">
          <span>{{ data.shop_name }}</span>
          <span class="dim">{{ data.company_name }}</span>
          <span class="dim">联系人 {{ data.contact_name || '—' }}</span>
        </div>
        <div class="dim mono">SPU {{ data.id }} · 编号 {{ data.platform_no }}</div>
      </div>

      <!-- 规格切换：原站就是这样，一款下面几十个规格靠这排小图来回切 -->
      <div class="skubar">
        <div class="skubar-h">
          规格 <b>{{ data.skus.length }}</b> 个
          <span v-if="activeSku" class="cur">当前：{{ activeSku.model || '（无型号）' }}
            · {{ price(activeSku) }}
            <template v-if="activeSku.color_name"> · {{ activeSku.color_name }}</template>
          </span>
        </div>
        <div class="skus">
          <button v-for="s in data.skus" :key="s.id" class="sku"
                  :class="{ on: activeSku && activeSku.id === s.id }"
                  :title="`${s.model || '无型号'} · ${price(s)}`"
                  @click="activeSku = s">
            <img v-if="skuThumb(s)" :src="skuThumb(s)" loading="lazy" @error="$event.target.style.visibility='hidden'" />
            <span class="lbl">{{ s.model || '—' }}</span>
          </button>
        </div>
      </div>

      <div class="cols">
        <section class="left">
          <div v-for="g in groups" :key="g.kind" class="imgs">
            <h3>
              {{ g.label }}
              <span v-if="g.kind === 'cover' || g.kind === 'dim'" class="dim">
                当前规格 {{ g.items.length }} 张 ·
                整款 {{ allStats[g.kind].local }} / {{ allStats[g.kind].total }} 已下载
              </span>
              <span v-else class="dim">
                {{ g.items.filter((i) => i.local).length }} / {{ g.items.length }} 已下载
              </span>
            </h3>
            <div class="strip">
              <template v-for="img in g.items" :key="img.id">
                <img v-if="img.local" :src="`/img?u=${encodeURIComponent(img.url)}`"
                     loading="lazy" :title="img.oss_name" />
                <div v-else class="miss" :title="`本地没有：${img.oss_name}`">未下载</div>
              </template>
            </div>
          </div>
          <p v-if="!groups.length" class="dim">该规格没有图片记录</p>
        </section>

        <aside class="right">
          <!-- 色系存的是雪花号，关联字典后才有名字和色卡图 -->
          <template v-if="activeSku">
            <h3>本规格</h3>
            <table class="kv">
              <tr><td class="k">型号</td><td>{{ activeSku.model || '—' }}</td></tr>
              <tr><td class="k">色系</td><td>
                <span v-if="activeSku.color_name" class="colr">
                  <img v-if="activeSku.color_icon"
                       :src="`/img?u=${encodeURIComponent(activeSku.color_icon)}&cdn=1`" />
                  {{ activeSku.color_name }}
                </span>
                <span v-else>—</span>
              </td></tr>
              <tr><td class="k">价格</td><td>{{ price(activeSku) }}</td></tr>
              <tr><td class="k">可索样</td><td>{{ activeSku.is_sample ? '是' : '否' }}</td></tr>
            </table>
          </template>

          <template v-if="skuAttrs.length">
            <h3>规格参数</h3>
            <table class="kv">
              <tr v-for="a in skuAttrs" :key="a.attr_id">
                <td class="k">{{ a.name }}</td><td>{{ a.value || '—' }}</td>
              </tr>
            </table>
          </template>

          <template v-if="data.attrs.length">
            <h3>商品参数</h3>
            <table class="kv">
              <tr v-for="a in data.attrs" :key="a.attr_id">
                <td class="k">{{ a.name }}</td><td>{{ a.value || '—' }}</td>
              </tr>
            </table>
          </template>

          <h3>基本信息</h3>
          <table class="kv">
            <tr><td class="k">计量单位</td><td>{{ data.measurement_unit || '—' }}</td></tr>
            <tr><td class="k">品牌</td><td>{{ data.brand_name || '—' }}</td></tr>
            <tr><td class="k">产地</td><td>
              {{ data.origin_name || '—' }}
              <span class="dim">（{{ data.domestic_or_imported === 'imported' ? '进口' : '国产' }}）</span>
            </td></tr>
            <tr><td class="k">可定制</td><td>{{ data.has_custom ? '是' : '否' }}</td></tr>
            <tr><td class="k">浏览量</td><td>{{ data.browse_count }}</td></tr>
            <tr v-if="data.description"><td class="k">规格描述</td><td>{{ data.description }}</td></tr>
          </table>
        </aside>
      </div>
    </div>

    <div v-else class="pad dim">没找到这个商品</div>
  </el-drawer>
</template>

<style scoped>
.pad { padding: 24px; }
.body { padding: 4px 4px 24px; }
.head h2 { margin: 4px 0 6px; font-size: 19px; display: flex; align-items: center; gap: 8px; }
.crumb { font-size: 12px; }
.sub { display: flex; gap: 12px; align-items: center; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }

.skubar { margin: 14px 0; padding: 12px; background: #f7f8fa; border-radius: 8px; }
.skubar-h { margin-bottom: 8px; }
.skubar-h .cur { color: #2f6feb; margin-left: 8px; }
.skus { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.sku { flex: none; width: 76px; border: 2px solid transparent; border-radius: 8px;
       background: #fff; padding: 4px; cursor: pointer; }
.sku:hover { border-color: #c9d6ee; }
.sku.on { border-color: #2f6feb; }
.sku img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 5px;
           background: #eef0f3; display: block; }
.sku .lbl { display: block; font-size: 11px; margin-top: 3px; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; color: #646a73; }

.cols { display: flex; gap: 14px; align-items: flex-start; }
.left { flex: 1; min-width: 0; }
.right { width: 300px; flex: none; }
.imgs { background: #f7f8fa; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.imgs h3 { margin: 0 0 10px; font-size: 14px; display: flex; gap: 8px; align-items: baseline; }
.strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.strip img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 6px;
             background: #eef0f3; display: block; }
.strip .miss { display: grid; place-items: center; aspect-ratio: 1; border-radius: 6px;
               color: #d97706; font-size: 13px;
               background: repeating-linear-gradient(45deg,
                 #fff7ed, #fff7ed 8px, #ffedd5 8px, #ffedd5 16px); }

h3 { font-size: 14px; margin: 12px 0 8px; }
.kv { width: 100%; border-collapse: collapse; font-size: 13px; }
.kv td { padding: 5px 0; border-bottom: 1px solid #ebedf0; vertical-align: top; }
.kv .k { color: #646a73; width: 92px; }
.dim { color: #a3a8b0; font-size: 12px; font-weight: normal; }
.dtitle { font-weight: 600; }
.colr { display: inline-flex; align-items: center; gap: 6px; }
.colr img { width: 18px; height: 18px; border-radius: 3px; object-fit: cover; }
</style>
